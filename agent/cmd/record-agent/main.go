// record-agent is the entrypoint for the dopl DaemonSet agent.
//
// Phase 1 responsibilities:
//   - Load configuration from environment variables.
//   - Initialise the disk spool (create dir, recover stale tmp dirs, scan bytes).
//   - Build the ingest pipeline (validator → queue → ingestor).
//   - Start the session manager worker (reads queue, commits bundles to spool).
//   - Serve HTTP on cfg.ListenAddress: POST /v1/events, GET /live, GET /ready.
//   - Shutdown gracefully on SIGTERM / SIGINT.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/dopl-dev/agent/internal/config"
	"github.com/dopl-dev/agent/internal/health"
	"github.com/dopl-dev/agent/internal/ingest"
	"github.com/dopl-dev/agent/internal/logging"
	"github.com/dopl-dev/agent/internal/session"
	"github.com/dopl-dev/agent/internal/spool"
)

func main() {
	// ── 1. Load and validate configuration ──────────────────────────────────
	cfg, err := config.Load()
	if err != nil {
		// Logger not yet initialised; write directly to stderr.
		fmt.Fprintf(os.Stderr, `{"level":"ERROR","msg":"config load failed","error":%q}`+"\n", err.Error())
		os.Exit(1)
	}

	// ── 2. Initialise logger ─────────────────────────────────────────────────
	log := logging.New(cfg.LogLevel)

	log.Info("record-agent starting",
		"listen_addr", cfg.ListenAddress,
		"spool_dir", cfg.SpoolDir,
		"max_spool_bytes", cfg.MaxSpoolBytes,
		"max_active_sessions", cfg.MaxActiveSessions,
		"max_session_bytes", cfg.MaxSessionBytes,
		"max_session_age", cfg.MaxSessionAge,
		"queue_size", cfg.IngestQueueSize,
		"max_event_bytes", cfg.MaxEventBytes,
		"max_batch_bytes", cfg.MaxBatchBytes,
		"log_level", cfg.LogLevel,
	)

	// ── 3. Initialise spool ──────────────────────────────────────────────────
	sp, err := spool.New(spool.SpoolConfig{
		SpoolDir:      cfg.SpoolDir,
		MaxSpoolBytes: cfg.MaxSpoolBytes,
	})
	if err != nil {
		log.Error("spool initialisation failed", "error", err)
		os.Exit(1)
	}
	log.Info("spool initialised",
		"spool_dir", cfg.SpoolDir,
		"current_bytes", sp.Stats().SpoolBytes,
	)

	// ── 4. Build ingest pipeline ─────────────────────────────────────────────
	validator := ingest.NewValidator(ingest.ValidatorConfig{
		MaxEventBytes:    cfg.MaxEventBytes,
		SupportedSchemas: []int{1},
	})
	queue := ingest.NewQueue(cfg.IngestQueueSize)
	ingestor := ingest.NewIngestor(validator, queue)

	// ── 5. Build session manager ─────────────────────────────────────────────
	sessionMgr := session.NewManager(
		session.Config{
			MaxActiveSessions: cfg.MaxActiveSessions,
			MaxSessionBytes:   cfg.MaxSessionBytes,
			MaxSessionAge:     cfg.MaxSessionAge,
		},
		sp,
		log,
	)

	// ── 6. Build health ──────────────────────────────────────────────────────
	// *spool.Spool exposes health stats via Stats(), not via direct interface
	// methods. The thin adapter below bridges the two without modifying the
	// spool package.
	h := health.New(
		health.Deps{
			Ingest:  ingestor,
			Spool:   &spoolHealthAdapter{sp: sp},
			Session: sessionMgr,
		},
		health.Config{
			QueuePct: 0.9,
			SpoolPct: 0.9,
		},
	)

	// ── 7. Start session worker ──────────────────────────────────────────────
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	workerDone := make(chan struct{})
	go func() {
		defer close(workerDone)
		sessionMgr.Run(ctx, ingestor.Events())
	}()

	// ── 8. Start HTTP server ─────────────────────────────────────────────────
	router := buildRouter(ingestor, h, cfg, log)
	srv := &http.Server{
		Addr:         cfg.ListenAddress,
		Handler:      router,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 15 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	serverErr := make(chan error, 1)
	go func() {
		log.Info("HTTP server listening", "addr", cfg.ListenAddress)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErr <- err
		}
	}()

	// ── 9. Wait for shutdown signal ──────────────────────────────────────────
	select {
	case sig := <-waitForShutdown():
		log.Info("shutdown signal received", "signal", sig.String())
	case err := <-serverErr:
		log.Error("HTTP server fatal error", "error", err)
	}

	// ── 10. Graceful shutdown ────────────────────────────────────────────────
	log.Info("shutting down")

	// Stop accepting new HTTP requests (10 s drain window).
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Warn("HTTP server shutdown error", "error", err)
	}

	// Cancel the session worker context so Run() exits and calls flushAll().
	cancel()
	select {
	case <-workerDone:
		log.Info("session worker stopped")
	case <-time.After(15 * time.Second):
		log.Warn("session worker did not stop within deadline")
	}

	log.Info("record-agent stopped")
}

// buildRouter wires the HTTP mux and wraps it with the middleware stack.
func buildRouter(ingestor *ingest.Ingestor, h *health.Health, cfg *config.Config, log *slog.Logger) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("POST /v1/events", eventsHandler(ingestor, cfg, log))
	mux.Handle("GET /live", health.LiveHandler(h))
	mux.Handle("GET /ready", health.ReadyHandler(h))

	// Middleware stack (outermost applied last).
	var handler http.Handler = mux
	handler = panicRecoveryMiddleware(handler, log)
	handler = requestLoggingMiddleware(handler, log)
	// Body limit wraps the full mux so it applies to all routes.
	handler = http.MaxBytesHandler(handler, cfg.MaxBatchBytes)

	return handler
}

// eventsHandler handles POST /v1/events.
func eventsHandler(ingestor *ingest.Ingestor, cfg *config.Config, log *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		req, err := ingest.DecodeRequestFromHTTP(r, w, cfg.MaxBatchBytes)
		if err != nil {
			var tooLarge *ingest.BodyTooLargeError
			if errors.As(err, &tooLarge) {
				writeJSONError(w, http.StatusRequestEntityTooLarge, "request body too large")
				return
			}
			writeJSONError(w, http.StatusBadRequest, err.Error())
			return
		}

		resp := ingestor.IngestBatch(req)

		if ingestor.ShouldLogDrop("queue_full") && resp.Dropped > 0 {
			log.Warn("events dropped",
				"accepted", resp.Accepted,
				"dropped", resp.Dropped,
				"by_reason", resp.DroppedByReason,
			)
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(resp)
	}
}

// panicRecoveryMiddleware catches panics in downstream handlers, logs them, and
// returns a 500 so the server process stays alive.
func panicRecoveryMiddleware(next http.Handler, log *slog.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				log.Error("handler panic recovered",
					"method", r.Method,
					"path", r.URL.Path,
					"panic", fmt.Sprintf("%v", rec),
				)
				http.Error(w, `{"error":"internal server error"}`, http.StatusInternalServerError)
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// requestLoggingMiddleware logs the method, path, and latency of each request.
// Probe endpoints (/live, /ready) are logged at Debug to avoid noise.
func requestLoggingMiddleware(next http.Handler, log *slog.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rw := &responseWriter{ResponseWriter: w, code: http.StatusOK}
		next.ServeHTTP(rw, r)
		latency := time.Since(start)

		logFn := log.Info
		if r.URL.Path == "/live" || r.URL.Path == "/ready" {
			logFn = log.Debug
		}
		logFn("http request",
			"method", r.Method,
			"path", r.URL.Path,
			"status", rw.code,
			"latency_ms", latency.Milliseconds(),
		)
	})
}

// responseWriter wraps http.ResponseWriter to capture the status code.
type responseWriter struct {
	http.ResponseWriter
	code int
}

func (rw *responseWriter) WriteHeader(code int) {
	rw.code = code
	rw.ResponseWriter.WriteHeader(code)
}

// waitForShutdown blocks until SIGTERM or SIGINT is received and returns the
// signal. Using a channel of size 1 ensures the signal is not lost if the
// goroutine is slightly slow to start.
func waitForShutdown() <-chan os.Signal {
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, syscall.SIGTERM, syscall.SIGINT)
	return ch
}

// writeJSONError writes a JSON error body with the given HTTP status code.
func writeJSONError(w http.ResponseWriter, code int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_, _ = fmt.Fprintf(w, `{"error":%q}`+"\n", msg)
}

// spoolHealthAdapter bridges *spool.Spool to health.SpoolStatus. The spool
// package exposes its status through a Stats() snapshot rather than direct
// interface methods, so this adapter translates between the two shapes without
// requiring changes to either package.
type spoolHealthAdapter struct {
	sp *spool.Spool
}

func (a *spoolHealthAdapter) SpoolBytes() int64    { return a.sp.Stats().SpoolBytes }
func (a *spoolHealthAdapter) MaxSpoolBytes() int64 { return a.sp.Stats().MaxSpoolBytes }
func (a *spoolHealthAdapter) Writable() bool       { return a.sp.Stats().Writable }
func (a *spoolHealthAdapter) LastError() string    { return a.sp.Stats().LastError }
