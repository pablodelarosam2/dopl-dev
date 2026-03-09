// Package health implements HTTP /live and /ready probe handlers.
package health

import "net/http"

// Status represents the health state of a single check.
type Status string

const (
	StatusOK       Status = "ok"
	StatusDegraded Status = "degraded"
)

// CheckResult is the outcome of a single readiness check.
// Name and Status are both of type Status (which is a string alias) so that
// the JSON field values are bounded to known strings.
type CheckResult struct {
	Name    string `json:"name"`
	Status  Status `json:"status"`
	Message string `json:"message,omitempty"`
}

// ReadinessReport is the JSON body returned by GET /ready.
type ReadinessReport struct {
	Status string        `json:"status"`
	Checks []CheckResult `json:"checks"`
}

// IngestStatus is the subset of ingest.Ingestor surface the health module needs.
type IngestStatus interface {
	QueueDepth() int
	QueueCapacity() int
}

// SpoolStatus is the subset of spool.Spool surface the health module needs.
type SpoolStatus interface {
	SpoolBytes() int64
	MaxSpoolBytes() int64
	Writable() bool
	LastError() string
}

// SessionStatus is optional; pass nil when the session manager is not wired.
type SessionStatus interface {
	ActiveSessions() int
	MaxActiveSessions() int
}

// UploaderStatus is optional; pass nil when the uploader is not enabled.
type UploaderStatus interface {
	Running() bool
	UploadsCompleted() int64
	UploadsFailed() int64
	LastError() string
}

// Deps bundles the status providers injected into Health.
type Deps struct {
	Ingest   IngestStatus
	Spool    SpoolStatus
	Session  SessionStatus  // may be nil
	Uploader UploaderStatus // may be nil
}

// Config controls the thresholds at which checks flip to degraded.
type Config struct {
	// QueuePct is the fraction [0,1) of queue capacity that triggers degraded.
	QueuePct float64
	// SpoolPct is the fraction [0,1) of spool capacity that triggers degraded.
	SpoolPct float64
}

// Health evaluates liveness and readiness probes.
type Health struct {
	ingest   IngestStatus
	spool    SpoolStatus
	session  SessionStatus
	uploader UploaderStatus
	cfg      Config
}

// New constructs a Health with the provided dependency and threshold config.
func New(deps Deps, cfg Config) *Health {
	return &Health{
		ingest:   deps.Ingest,
		spool:    deps.Spool,
		session:  deps.Session,
		uploader: deps.Uploader,
		cfg:      cfg,
	}
}

// Live always returns 200. The liveness probe only fails if the process is
// dead — no internal state is checked here.
func (h *Health) Live() (code int, body any) {
	return http.StatusOK, "ok"
}

// Ready evaluates all registered checks. Returns 200 when all pass, 503 when
// any check is degraded. The body always contains the full check list so
// callers can see which subsystem tripped the probe.
//
// Nil deps are skipped gracefully so that zero-value Health structs (e.g. in
// unit tests that only exercise the struct layout) do not panic.
func (h *Health) Ready() (code int, report ReadinessReport) {
	var checks []CheckResult
	overallOK := true

	if h.spool != nil {
		// --- spool writable ---
		if !h.spool.Writable() {
			msg := h.spool.LastError()
			if msg == "" {
				msg = "spool not writable"
			}
			checks = append(checks, CheckResult{
				Name:    "spool_not_writable",
				Status:  StatusDegraded,
				Message: msg,
			})
			overallOK = false
		} else {
			checks = append(checks, CheckResult{Name: "spool_not_writable", Status: StatusOK})
		}

		// --- spool pressure ---
		spoolPct := ratio(h.spool.SpoolBytes(), h.spool.MaxSpoolBytes())
		if spoolPct >= h.cfg.SpoolPct {
			checks = append(checks, CheckResult{
				Name:    "spool_pressure",
				Status:  StatusDegraded,
				Message: pctMessage("spool", spoolPct),
			})
			overallOK = false
		} else {
			checks = append(checks, CheckResult{Name: "spool_pressure", Status: StatusOK})
		}
	}

	if h.ingest != nil {
		// --- queue pressure ---
		queuePct := ratio(int64(h.ingest.QueueDepth()), int64(h.ingest.QueueCapacity()))
		if queuePct >= h.cfg.QueuePct {
			checks = append(checks, CheckResult{
				Name:    "queue_pressure",
				Status:  StatusDegraded,
				Message: pctMessage("ingest queue", queuePct),
			})
			overallOK = false
		} else {
			checks = append(checks, CheckResult{Name: "queue_pressure", Status: StatusOK})
		}
	}

	// --- session pressure (optional) ---
	if h.session != nil {
		sessPct := ratio(int64(h.session.ActiveSessions()), int64(h.session.MaxActiveSessions()))
		if sessPct >= h.cfg.QueuePct {
			checks = append(checks, CheckResult{
				Name:    "session_pressure",
				Status:  StatusDegraded,
				Message: pctMessage("active sessions", sessPct),
			})
			overallOK = false
		} else {
			checks = append(checks, CheckResult{Name: "session_pressure", Status: StatusOK})
		}
	}

	// --- uploader (optional) ---
	if h.uploader != nil {
		if !h.uploader.Running() {
			msg := h.uploader.LastError()
			if msg == "" {
				msg = "uploader not running"
			}
			checks = append(checks, CheckResult{
				Name:    "uploader",
				Status:  StatusDegraded,
				Message: msg,
			})
			overallOK = false
		} else if lastErr := h.uploader.LastError(); lastErr != "" {
			checks = append(checks, CheckResult{
				Name:    "uploader",
				Status:  StatusDegraded,
				Message: lastErr,
			})
			overallOK = false
		} else {
			checks = append(checks, CheckResult{Name: "uploader", Status: StatusOK})
		}
	}

	status := string(StatusOK)
	httpCode := http.StatusOK
	if !overallOK {
		status = string(StatusDegraded)
		httpCode = http.StatusServiceUnavailable
	}
	return httpCode, ReadinessReport{Status: status, Checks: checks}
}

// ratio computes a/b as a float64, returning 0 when b == 0 to avoid division
// by zero on startup before any capacity is established.
func ratio(a, b int64) float64 {
	if b == 0 {
		return 0
	}
	return float64(a) / float64(b)
}

func pctMessage(label string, pct float64) string {
	return label + " at " + formatPct(pct) + " capacity"
}

func formatPct(f float64) string {
	// Inline integer-only formatting to avoid importing fmt in a hot path.
	pct := int(f * 100)
	if pct >= 100 {
		return "100%"
	}
	tens := pct / 10
	ones := pct % 10
	digits := []byte{byte('0' + tens), byte('0' + ones), '%'}
	return string(digits)
}
