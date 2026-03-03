// Package session manages the lifecycle of a single recording session,
// correlating inbound records with a request ID and closing the session when
// the request completes.
package session

import (
	"context"
	"encoding/json"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/dopl-dev/agent/internal/ingest"
	"github.com/dopl-dev/agent/internal/spool"
)

// Config controls session lifecycle limits.
type Config struct {
	// MaxActiveSessions is the maximum number of in-flight sessions. Events for
	// a new session are dropped when this limit is reached.
	MaxActiveSessions int

	// MaxSessionBytes is the per-session byte budget. When a session's
	// accumulated payload bytes exceed this value it is force-committed even
	// if no Output event has arrived.
	MaxSessionBytes int64

	// MaxSessionAge is the wall-clock deadline for a session. Sessions that
	// have not received an Output event within this window are force-committed
	// by the expiry sweep.
	MaxSessionAge time.Duration
}

// sessionState holds all data accumulated for a single in-flight session.
type sessionState struct {
	sessionID     string
	fixtureID     string
	schemaVersion int
	service       string

	// Payload fields — populated as events arrive. Last-write-wins for single-
	// valued fields; stubs and metadata accumulate across multiple events.
	input       json.RawMessage
	goldenOutput json.RawMessage
	stubs       []json.RawMessage
	metadata    []json.RawMessage

	totalBytes  int64
	createdAt   time.Time
	lastEventAt time.Time
}

// Committer is the subset of spool.Spool that the Manager calls. Defined as an
// interface to allow test doubles without pulling in the full spool package.
type Committer interface {
	Commit(bundle spool.FixtureBundle) (spool.WriteResult, error)
}

// Manager consumes events from the ingest queue, aggregates them by session,
// and commits completed sessions to the spool.
type Manager struct {
	cfg Config
	sp  Committer
	log *slog.Logger

	mu     sync.Mutex
	active map[string]*sessionState

	// activeSessions mirrors len(active) but can be read without the lock by
	// the health module.
	activeSessions atomic.Int64
}

// NewManager constructs a Manager. sp must be non-nil.
func NewManager(cfg Config, sp Committer, log *slog.Logger) *Manager {
	return &Manager{
		cfg:    cfg,
		sp:     sp,
		log:    log,
		active: make(map[string]*sessionState),
	}
}

// Run is the single goroutine that consumes events. It exits when ctx is
// cancelled or events is closed, flushing all in-flight sessions before
// returning.
//
// Call as:  go mgr.Run(ctx, ingestor.Events())
func (m *Manager) Run(ctx context.Context, events <-chan ingest.Envelope) {
	// Sweep at half the max age so sessions don't overstay by a full period.
	ticker := time.NewTicker(m.cfg.MaxSessionAge / 2)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			m.flushAll()
			return

		case e, ok := <-events:
			if !ok {
				m.flushAll()
				return
			}
			m.handleEvent(e)

		case <-ticker.C:
			m.sweepExpired()
		}
	}
}

// ActiveSessions returns the current number of in-flight sessions.
// Implements health.SessionStatus.
func (m *Manager) ActiveSessions() int {
	return int(m.activeSessions.Load())
}

// MaxActiveSessions returns the configured session cap.
// Implements health.SessionStatus.
func (m *Manager) MaxActiveSessions() int {
	return m.cfg.MaxActiveSessions
}

// handleEvent routes one validated event to its session bucket.
func (m *Manager) handleEvent(e ingest.Envelope) {
	m.mu.Lock()
	defer m.mu.Unlock()

	s, exists := m.active[e.SessionID]
	if !exists {
		if int(m.activeSessions.Load()) >= m.cfg.MaxActiveSessions {
			m.log.Warn("session capacity reached, dropping event",
				"session_id", e.SessionID,
				"fixture_id", e.FixtureID,
				"event_type", e.EventType,
				"active_sessions", m.activeSessions.Load(),
				"max_sessions", m.cfg.MaxActiveSessions,
			)
			return
		}
		s = &sessionState{
			sessionID:     e.SessionID,
			fixtureID:     e.FixtureID,
			schemaVersion: e.SchemaVersion,
			service:       e.Service,
			createdAt:     time.Now(),
		}
		m.active[e.SessionID] = s
		m.activeSessions.Add(1)
	}

	s.lastEventAt = time.Now()
	payloadBytes := int64(len(e.Payload))

	switch e.EventType {
	case ingest.EventTypeInput:
		s.input = e.Payload
	case ingest.EventTypeStub:
		s.stubs = append(s.stubs, e.Payload)
	case ingest.EventTypeOutput:
		s.goldenOutput = e.Payload
		s.totalBytes += payloadBytes
		// Happy-path completion: Output event arrived.
		m.commitAndDelete(e.SessionID, s, "output_received")
		return
	case ingest.EventTypeMetadata:
		s.metadata = append(s.metadata, e.Payload)
	}

	s.totalBytes += payloadBytes

	// Safety valve: force-flush if the session has grown too large.
	if s.totalBytes > m.cfg.MaxSessionBytes {
		m.log.Warn("session exceeded byte limit, force-committing",
			"session_id", e.SessionID,
			"total_bytes", s.totalBytes,
			"max_bytes", m.cfg.MaxSessionBytes,
		)
		m.commitAndDelete(e.SessionID, s, "size_overflow")
	}
}

// sweepExpired commits sessions that have not received an Output event within
// MaxSessionAge. Called on a ticker from the Run loop.
func (m *Manager) sweepExpired() {
	now := time.Now()
	m.mu.Lock()
	defer m.mu.Unlock()

	for id, s := range m.active {
		if now.Sub(s.createdAt) >= m.cfg.MaxSessionAge {
			m.log.Warn("session expired, force-committing",
				"session_id", id,
				"age_ms", now.Sub(s.createdAt).Milliseconds(),
				"max_age_ms", m.cfg.MaxSessionAge.Milliseconds(),
			)
			m.commitAndDelete(id, s, "expired")
		}
	}
}

// flushAll commits every in-flight session on shutdown. Best-effort: errors
// are logged but do not block the shutdown path.
func (m *Manager) flushAll() {
	m.mu.Lock()
	defer m.mu.Unlock()

	for id, s := range m.active {
		m.commitAndDelete(id, s, "shutdown_flush")
	}
}

// commitAndDelete builds a FixtureBundle, calls spool.Commit, logs the result,
// and removes the session from the active map. Must be called with m.mu held.
func (m *Manager) commitAndDelete(id string, s *sessionState, reason string) {
	bundle := m.buildBundle(s)

	result, err := m.sp.Commit(bundle)
	if err != nil {
		m.log.Error("spool commit error",
			"session_id", id,
			"fixture_id", s.fixtureID,
			"reason", reason,
			"error", err,
		)
	} else if result.Dropped {
		m.log.Warn("spool commit dropped",
			"session_id", id,
			"fixture_id", s.fixtureID,
			"reason", reason,
			"drop_reason", result.DropReason,
		)
	} else {
		m.log.Info("session committed",
			"session_id", id,
			"fixture_id", s.fixtureID,
			"reason", reason,
			"bytes_written", result.BytesWritten,
		)
	}

	delete(m.active, id)
	m.activeSessions.Add(-1)
}

// buildBundle assembles a spool.FixtureBundle from the accumulated session
// state. Multi-event fields (stubs, metadata) are marshalled into JSON arrays.
func (m *Manager) buildBundle(s *sessionState) spool.FixtureBundle {
	bundle := spool.FixtureBundle{
		SchemaVersion: s.schemaVersion,
		FixtureID:     s.fixtureID,
		SessionID:     s.sessionID,
		CreatedAtMs:   s.createdAt.UnixMilli(),
		Input:         s.input,
		GoldenOutput:  s.goldenOutput,
	}

	// Encode the service name as a JSON string so it fits in json.RawMessage.
	if s.service != "" {
		if raw, err := json.Marshal(s.service); err == nil {
			bundle.Service = raw
		}
	}

	// Multiple stubs are emitted as a JSON array.
	if len(s.stubs) > 0 {
		if raw, err := json.Marshal(s.stubs); err == nil {
			bundle.Stubs = raw
		}
	}

	// Multiple metadata events are emitted as a JSON array.
	if len(s.metadata) > 0 {
		if raw, err := json.Marshal(s.metadata); err == nil {
			bundle.Metadata = raw
		}
	}

	return bundle
}
