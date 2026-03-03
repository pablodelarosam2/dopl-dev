package spool

// This file contains the cached-state accessors on Writer that implement
// the health.SpoolStatus interface:
//
//	SpoolBytes() int64
//	MaxSpoolBytes() int64
//	Writable() bool
//	LastError() string
//
// All values are maintained incrementally by WriteFixture / EnsureCapacity /
// InitScan so that health probes never need to walk the filesystem.

// SpoolBytes returns the current total bytes occupied by committed fixtures.
// Updated atomically after every write or eviction.
func (w *Writer) SpoolBytes() int64 {
	return w.spoolBytesCurrent.Load()
}

// MaxSpoolBytes returns the configured hard cap on spool usage.
func (w *Writer) MaxSpoolBytes() int64 {
	return w.cfg.MaxSpoolBytes
}

// Writable reports whether the last I/O operation succeeded. When false, the
// health probe should report the spool as degraded.
func (w *Writer) Writable() bool {
	return w.writable.Load()
}

// LastError returns the most recent I/O error message, or "" if no error.
func (w *Writer) LastError() string {
	w.errMu.Lock()
	defer w.errMu.Unlock()
	return w.lastError
}

// setError records an I/O failure and marks the writer as non-writable.
func (w *Writer) setError(err error) {
	w.writable.Store(false)
	w.errMu.Lock()
	w.lastError = err.Error()
	w.errMu.Unlock()
}

// clearError resets the error state and marks the writer as writable.
func (w *Writer) clearError() {
	w.writable.Store(true)
	w.errMu.Lock()
	w.lastError = ""
	w.errMu.Unlock()
}
