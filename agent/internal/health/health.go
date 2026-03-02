// Package health implements HTTP /healthz and /readyz probe handlers.
package health

import "net/http"

// type Status string with values: ok, degraded
type Status string
const (
	StatusOK Status = "ok"
	StatusDegraded Status = "degraded"
)
type CheckResult struct { Name, Status Status, Message string }
type ReadinessReport struct { Status string; Checks []CheckResult }

type IngestStatus interface { QueueDepth() int; QueueCapacity() int }
type SpoolStatus interface { SpoolBytes() int64; MaxSpoolBytes() int64; Writable() bool; LastError() string }
type SessionStatus interface { ActiveSessions() int; MaxActiveSessions() int } // optional

type Health struct { ingest IngestStatus; spool SpoolStatus; session SessionStatus; criticalPct float64 }
func (h *Health) Live() (code int, body any) { return http.StatusOK, "ok" }
func (h *Health) Ready() (code int, report ReadinessReport) { return http.StatusOK, ReadinessReport{ Status: string(StatusOK), Checks: []CheckResult{} } }
