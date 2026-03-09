// record-agent is the entrypoint for the dopl DaemonSet agent.
//
// Responsibilities:
//   - Load configuration from environment variables.
//   - Initialise the disk spool (create dir, recover stale tmp dirs, scan bytes).
//   - Build the ingest pipeline (validator → queue → ingestor).
//   - Start the session manager worker (reads queue, commits bundles to spool).
//   - Optionally start the S3 uploader (when AGENT_S3_BUCKET is set).
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

	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/dopl-dev/agent/internal/config"
	"github.com/dopl-dev/agent/internal/health"
	"github.com/dopl-dev/agent/internal/ingest"
	"github.com/dopl-dev/agent/internal/logging"
	"github.com/dopl-dev/agent/internal/session"
	"github.com/dopl-dev/agent/internal/spool"
	"github.com/dopl-dev/agent/internal/uploader"
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

	// ── 6. Build uploader (optional) ────────────────────────────────────────
	var up *uploader.Uploader
	if cfg.S3Bucket != "" {
		awsCfg, err := awsconfig.LoadDefaultConfig(context.Background(),
			awsconfig.WithRegion(cfg.S3Region),
		)
		if err != nil {
			log.Error("failed to load AWS config", "error", err)
			os.Exit(1)
		}
		s3Client := s3.NewFromConfig(awsCfg)

		up, err = uploader.New(
			&s3ClientAdapter{client: s3Client, bucket: cfg.S3Bucket},
			sp,
			uploader.UploaderConfig{
				Bucket:       cfg.S3Bucket,
				Region:       cfg.S3Region,
				Prefix:       cfg.S3Prefix,
				Workers:      cfg.UploadWorkers,
				ScanInterval: cfg.UploadInterval,
				MaxRetries:   cfg.UploadMaxRetries,
			},
			log,
		)
		if err != nil {
			log.Error("uploader initialisation failed", "error", err)
			os.Exit(1)
		}
		log.Info("uploader configured",
			"bucket", cfg.S3Bucket,
			"region", cfg.S3Region,
			"prefix", cfg.S3Prefix,
			"workers", cfg.UploadWorkers,
		)
	} else {
		log.Info("uploader disabled (AGENT_S3_BUCKET not set)")
	}

	// ── 7. Build health ─────────────────────────────────────────────────────
	// *spool.Spool exposes health stats via Stats(), not via direct interface
	// methods. The thin adapter below bridges the two without modifying the
	// spool package.
	healthDeps := health.Deps{
		Ingest:  ingestor,
		Spool:   &spoolHealthAdapter{sp: sp},
		Session: sessionMgr,
	}
	if up != nil {
		healthDeps.Uploader = up
	}
	h := health.New(
		healthDeps,
		health.Config{
			QueuePct: 0.9,
			SpoolPct: 0.9,
		},
	)

	// ── 8. Start session worker ──────────────────────────────────────────────
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	workerDone := make(chan struct{})
	go func() {
		defer close(workerDone)
		sessionMgr.Run(ctx, ingestor.Events())
	}()

	// ── 8b. Start uploader (if configured) ──────────────────────────────────
	uploaderDone := make(chan struct{})
	if up != nil {
		go func() {
			defer close(uploaderDone)
			up.Run(ctx)
		}()
	} else {
		close(uploaderDone)
	}

	// ── 9. Start HTTP server ─────────────────────────────────────────────────
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

	// ── 10. Wait for shutdown signal ─────────────────────────────────────────
	select {
	case sig := <-waitForShutdown():
		log.Info("shutdown signal received", "signal", sig.String())
	case err := <-serverErr:
		log.Error("HTTP server fatal error", "error", err)
	}

	// ── 11. Graceful shutdown ────────────────────────────────────────────────
	log.Info("shutting down")

	// Stop accepting new HTTP requests (10 s drain window).
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Warn("HTTP server shutdown error", "error", err)
	}

	// Cancel the shared context so all background workers begin draining.
	cancel()

	// Wait for uploader to drain in-flight uploads (15 s deadline).
	select {
	case <-uploaderDone:
		log.Info("uploader stopped")
	case <-time.After(15 * time.Second):
		log.Warn("uploader did not stop within deadline")
	}

	// Wait for session worker to flush remaining sessions (15 s deadline).
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

// s3ClientAdapter bridges *s3.Client to uploader.S3Client. The bucket is
// baked in at construction time so individual PutObject calls only carry the
// key and body.
type s3ClientAdapter struct {
	client *s3.Client
	bucket string
}

func (a *s3ClientAdapter) PutObject(ctx context.Context, params *uploader.PutObjectInput) error {
	_, err := a.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:      &a.bucket,
		Key:         &params.Key,
		Body:        params.Body,
		ContentType: &params.ContentType,
	})
	return err
}
