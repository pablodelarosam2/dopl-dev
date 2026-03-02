// Package ingest implements the wire protocol server that receives fixture data
// from sim_sdk running inside application containers on the same node.
package ingest

import (
	"sync"
	"time"
)

// dropLogCooldown is the minimum time between log warnings for the same drop
// reason. Prevents log floods when a queue is persistently full or a bad
// client keeps sending invalid data.
const dropLogCooldown = 10 * time.Second

// Ingestor is the orchestration layer between the HTTP handler and the queue.
// It validates each incoming event, enqueues accepted events, and tallies
// results so the handler can return an accurate IngestResponse.
//
// Dependency graph:
//
//	HTTP handler
//	    │
//	    ▼
//	Ingestor.IngestBatch(req)
//	    │
//	    ├── Validator.Validate(e)   ── invalid → droppedByReason[reason]++
//	    │
//	    └── Queue.TryEnqueue(e)    ── full    → droppedByReason["queue_full"]++
//	                                ── ok     → accepted++
type Ingestor struct {
	validator   *Validator
	queue       *Queue

	// dropLog tracks the last time we emitted a warning for each drop reason,
	// so ShouldLogDrop can rate-limit noisy warnings.
	dropLogMu   sync.Mutex
	dropLogAt   map[string]time.Time
}

// NewIngestor wires a Validator and a Queue together into an Ingestor.
// Both must be non-nil; they are constructed by the caller so their configs
// (schema versions, queue capacity, byte limits) are decided at the
// application layer, not here.
func NewIngestor(v *Validator, q *Queue) *Ingestor {
	return &Ingestor{
		validator: v,
		queue:     q,
		dropLogAt: make(map[string]time.Time),
	}
}

// IngestBatch processes all events in req and returns a summary of what
// happened. It never returns an error — every event is either accepted,
// invalid, or dropped due to back-pressure, and the caller gets counts for
// all three outcomes.
//
// This design keeps the HTTP handler simple: it calls IngestBatch, gets an
// IngestResponse, and writes it as JSON. The handler never needs to know
// about schema versions or queue capacity.
func (in *Ingestor) IngestBatch(req IngestRequest) IngestResponse {
	resp := IngestResponse{
		DroppedByReason: make(map[string]int),
	}

	for i := range req.Events {
		// Use a pointer into the slice to avoid copying the Envelope twice
		// (once for Validate, once for the switch below). Envelopes can carry
		// a json.RawMessage payload that is a slice header — safe to share.
		e := &req.Events[i]

		result := in.validator.Validate(e)
		if !result.OK {
			resp.Invalid++
			resp.Dropped++
			resp.DroppedByReason[result.Reason]++
			continue
		}

		if !in.queue.TryEnqueue(*e) {
			resp.Dropped++
			resp.DroppedByReason["queue_full"]++
			continue
		}

		resp.Accepted++
	}

	return resp
}

// Events returns the read-only channel of validated, enqueued Envelopes.
// The session manager (or spool writer) reads from this channel to persist
// events to disk. Using the channel directly — rather than a callback —
// lets the consumer use a select loop alongside a shutdown signal.
func (in *Ingestor) Events() <-chan Envelope {
	return in.queue.Chan()
}

// QueueDepth returns the number of enqueued events not yet consumed.
// Exposed so the Health struct can implement IngestStatus without importing
// the queue directly.
func (in *Ingestor) QueueDepth() int {
	return in.queue.Depth()
}

// QueueCapacity returns the maximum queue size configured at startup.
// Also part of IngestStatus for health/readiness probes.
func (in *Ingestor) QueueCapacity() int {
	return in.queue.Capacity()
}

// ShouldLogDrop returns true if a warning for reason has not been emitted
// within the last dropLogCooldown window. Call this before emitting a log
// line on every drop to avoid flooding logs when the queue is persistently
// full or a client repeatedly sends invalid events.
//
// Example usage in a handler or worker:
//
//	if in.ShouldLogDrop(reason) {
//	    log.Printf("warn: dropping events: %s", reason)
//	}
func (in *Ingestor) ShouldLogDrop(reason string) bool {
	in.dropLogMu.Lock()
	defer in.dropLogMu.Unlock()

	last, seen := in.dropLogAt[reason]
	if !seen || time.Since(last) >= dropLogCooldown {
		in.dropLogAt[reason] = time.Now()
		return true
	}
	return false
}
