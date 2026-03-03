// Package spool handles buffering session data to local disk, including file
// rotation and cleanup of successfully uploaded sessions.
package spool

import "fmt"

// Spool is the public entry point for the spool subsystem. It owns the full
// lifecycle of the spool directory: startup recovery, atomic fixture commits,
// quota enforcement, and health/metrics reporting.
//
// Internally it delegates all filesystem operations to a Writer, which holds
// the concurrency lock and the cached byte counter. Spool itself adds no extra
// locking on top of Writer — callers may call Commit from multiple goroutines
// safely because Writer.WriteFixture is already mutex-protected.
//
// Typical usage in the agent startup sequence:
//
//	sp, err := spool.New(cfg)    // create + recover + scan
//	...
//	result, err := sp.Commit(bundle)
//	...
//	stats := sp.Stats()          // feed into health.Ready()
type Spool struct {
	writer *Writer
}

// SpoolStats is a point-in-time snapshot of spool health, returned by Stats().
// It mirrors the health.SpoolStatus interface so the health module can consume
// it without importing spool internals.
type SpoolStats struct {
	SpoolBytes    int64
	MaxSpoolBytes int64
	Writable      bool
	LastError     string
}

// New constructs a Spool from cfg, ensuring the spool directory exists,
// removing stale .tmp directories left by a previous crash, and computing
// the initial cached byte total from committed fixtures on disk.
//
// If any of those startup steps fail the error is returned immediately and
// the Spool is not usable.
func New(cfg SpoolConfig) (*Spool, error) {
	w, err := NewWriter(cfg)
	if err != nil {
		return nil, fmt.Errorf("spool writer: %w", err)
	}

	if err := w.InitScan(); err != nil {
		return nil, fmt.Errorf("spool init scan: %w", err)
	}

	return &Spool{writer: w}, nil
}

// Recover explicitly re-runs crash recovery (removes stale .tmp directories).
// New already calls this during startup; Recover is provided for cases where
// the operator wants to trigger recovery without restarting the process.
func (s *Spool) Recover() error {
	s.writer.mu.Lock()
	defer s.writer.mu.Unlock()

	if err := s.writer.RecoverTempDirs(); err != nil {
		s.writer.setError(err)
		return fmt.Errorf("recover temp dirs: %w", err)
	}
	return nil
}

// Commit persists a completed session fixture to disk atomically. It:
//  1. Validates the bundle and sanitizes the fixture ID.
//  2. Marshals the bundle to JSON exactly once (accurate quota accounting).
//  3. Evicts the oldest committed fixtures if necessary to stay within quota.
//  4. Writes to a .tmp staging directory, then renames it to the final path.
//  5. Updates the cached byte total.
//
// If the spool is full and eviction cannot free enough space, the fixture is
// dropped (best-effort). The returned WriteResult always describes what happened
// so the caller can increment its own drop counter if needed.
func (s *Spool) Commit(bundle FixtureBundle) (WriteResult, error) {
	return s.writer.WriteFixture(bundle)
}

// Stats returns a point-in-time snapshot of spool health metrics. The values
// are read from atomic/guarded fields maintained by the writer — no filesystem
// I/O occurs during this call, making it safe to call from health probes.
func (s *Spool) Stats() SpoolStats {
	return SpoolStats{
		SpoolBytes:    s.writer.SpoolBytes(),
		MaxSpoolBytes: s.writer.MaxSpoolBytes(),
		Writable:      s.writer.Writable(),
		LastError:     s.writer.LastError(),
	}
}

// CleanupIfNeeded triggers an explicit capacity check and evicts the oldest
// committed fixtures until the spool usage drops below the configured quota.
// In normal operation this is unnecessary — EnsureCapacity runs automatically
// inside Commit. Provide this for periodic maintenance jobs or operator tooling.
//
// Returns the total bytes freed and any filesystem error encountered.
func (s *Spool) CleanupIfNeeded() (freedBytes int64, err error) {
	s.writer.mu.Lock()
	defer s.writer.mu.Unlock()

	freed, _, err := s.writer.ensureCapacity(0)
	return freed, err
}

// List returns metadata for every committed fixture currently on disk.
// Entries are in filesystem-readdir order (roughly creation order).
// Useful for debugging, the future uploader, and tooling.
func (s *Spool) List() ([]FixtureInfo, error) {
	s.writer.mu.Lock()
	defer s.writer.mu.Unlock()

	return s.writer.listCommittedFixtures()
}
