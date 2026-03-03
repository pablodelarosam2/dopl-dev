package spool

import (
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
)

// Writer persists completed session fixtures to disk with atomic commit
// semantics and bounded disk usage. It is safe for concurrent use.
//
// The Writer also implements health.SpoolStatus via the accessor methods
// defined in state.go, so the health module can check spool pressure
// without importing spool internals.
type Writer struct {
	cfg SpoolConfig

	// mu serialises WriteFixture and EnsureCapacity so that quota accounting
	// is always consistent with what is actually on disk.
	mu sync.Mutex

	// --- cached state (see state.go for accessors) ---

	spoolBytesCurrent atomic.Int64
	writable          atomic.Bool

	errMu     sync.Mutex
	lastError string
}

// NewWriter creates a Writer for the given config and ensures the spool
// directory exists. It does NOT call InitScan — the caller should do that
// explicitly after construction so startup errors can be handled separately.
func NewWriter(cfg SpoolConfig) (*Writer, error) {
	if err := cfg.Validate(); err != nil {
		return nil, fmt.Errorf("spool config: %w", err)
	}

	if err := os.MkdirAll(cfg.SpoolDir, 0o755); err != nil {
		return nil, fmt.Errorf("create spool dir: %w", err)
	}

	w := &Writer{cfg: cfg}
	w.writable.Store(true)
	return w, nil
}

// WriteFixture persists a single FixtureBundle to disk atomically.
//
// The sequence is:
//  1. Validate the bundle and sanitize the fixture ID.
//  2. Marshal to JSON once (gives exact byte count for quota).
//  3. Ensure capacity (evict oldest fixtures if necessary).
//  4. Write to a temporary staging directory.
//  5. Rename staging dir to final dir (atomic commit).
//  6. Update cached spool bytes.
//
// If the spool is full and cannot be freed, the fixture is dropped (best-effort)
// and the result reports Dropped=true. I/O errors set the writer to non-writable
// so the health probe surfaces the failure.
func (w *Writer) WriteFixture(bundle FixtureBundle) (WriteResult, error) {
	if err := bundle.ValidateBasic(); err != nil {
		return WriteResult{}, fmt.Errorf("validate bundle: %w", err)
	}

	if err := SanitizeFixtureID(bundle.FixtureID); err != nil {
		return WriteResult{}, fmt.Errorf("sanitize fixture_id: %w", err)
	}

	data, err := json.Marshal(bundle)
	if err != nil {
		return WriteResult{}, fmt.Errorf("marshal fixture: %w", err)
	}

	needBytes := int64(len(data))

	w.mu.Lock()
	defer w.mu.Unlock()

	_, ok, err := w.ensureCapacity(needBytes)
	if err != nil {
		w.setError(err)
		return WriteResult{}, fmt.Errorf("ensure capacity: %w", err)
	}
	if !ok {
		return WriteResult{Dropped: true, DropReason: "spool_full"}, nil
	}

	tmpDir := TempFixtureDir(w.cfg.SpoolDir, bundle.FixtureID)
	finalDir := FixtureDir(w.cfg.SpoolDir, bundle.FixtureID)

	if err := os.MkdirAll(tmpDir, 0o755); err != nil {
		w.setError(err)
		return WriteResult{}, fmt.Errorf("create temp dir: %w", err)
	}

	tmpFile := FixtureFilePath(tmpDir)
	if err := os.WriteFile(tmpFile, data, 0o644); err != nil {
		_ = os.RemoveAll(tmpDir)
		w.setError(err)
		return WriteResult{}, fmt.Errorf("write fixture.json: %w", err)
	}

	if err := os.Rename(tmpDir, finalDir); err != nil {
		_ = os.RemoveAll(tmpDir)
		w.setError(err)
		return WriteResult{}, fmt.Errorf("commit fixture dir: %w", err)
	}

	w.spoolBytesCurrent.Add(needBytes)
	w.clearError()

	return WriteResult{BytesWritten: needBytes}, nil
}

// RecoverTempDirs removes any stale .tmp directories left behind by a
// previous crash or incomplete write. It is called by InitScan on startup.
func (w *Writer) RecoverTempDirs() error {
	entries, err := os.ReadDir(w.cfg.SpoolDir)
	if err != nil {
		return fmt.Errorf("read spool dir: %w", err)
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		if !IsTempDirName(entry.Name()) {
			continue
		}
		path := FixtureDir(w.cfg.SpoolDir, entry.Name())
		if err := os.RemoveAll(path); err != nil {
			return fmt.Errorf("remove stale temp dir %s: %w", entry.Name(), err)
		}
	}
	return nil
}
