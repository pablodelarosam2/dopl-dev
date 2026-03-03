package spool

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

// InitScan performs the one-time startup scan of the spool directory:
//  1. Removes stale .tmp directories left by a previous crash.
//  2. Computes the total size of committed fixture directories.
//  3. Initialises the cached spoolBytesCurrent counter.
//  4. Marks the writer as writable.
//
// Call this once after NewWriter, before accepting writes.
func (w *Writer) InitScan() error {
	w.mu.Lock()
	defer w.mu.Unlock()

	if err := w.RecoverTempDirs(); err != nil {
		w.setError(err)
		return fmt.Errorf("recover temp dirs: %w", err)
	}

	fixtures, err := w.listCommittedFixtures()
	if err != nil {
		w.setError(err)
		return fmt.Errorf("list committed fixtures: %w", err)
	}

	var totalBytes int64
	for _, fi := range fixtures {
		totalBytes += fi.SizeBytes
	}

	w.spoolBytesCurrent.Store(totalBytes)
	w.clearError()
	return nil
}

// ensureCapacity is the internal (mutex-held) implementation of capacity
// enforcement. It checks whether needBytes can fit within the quota and,
// if not, evicts the oldest committed fixtures until either enough space is
// freed or there is nothing left to evict.
//
// Returns:
//   - freed: total bytes reclaimed by eviction
//   - ok: true if needBytes now fits within the quota
//   - err: any filesystem error encountered during eviction
func (w *Writer) ensureCapacity(needBytes int64) (freed int64, ok bool, err error) {
	current := w.spoolBytesCurrent.Load()
	if current+needBytes <= w.cfg.MaxSpoolBytes {
		return 0, true, nil
	}

	fixtures, err := w.listCommittedFixtures()
	if err != nil {
		return 0, false, fmt.Errorf("list fixtures for eviction: %w", err)
	}

	// Oldest first — sort by modification time ascending.
	sort.Slice(fixtures, func(i, j int) bool {
		return fixtures[i].ModTime.Before(fixtures[j].ModTime)
	})

	for _, fi := range fixtures {
		if current+needBytes <= w.cfg.MaxSpoolBytes {
			break
		}

		deleted, delErr := w.deleteFixtureDir(fi.Path)
		if delErr != nil {
			return freed, false, fmt.Errorf("evict fixture %s: %w", fi.FixtureID, delErr)
		}

		freed += deleted
		current -= deleted
		w.spoolBytesCurrent.Add(-deleted)
	}

	return freed, current+needBytes <= w.cfg.MaxSpoolBytes, nil
}

// listCommittedFixtures reads the spool directory and returns metadata for
// every committed (non-.tmp) fixture directory. Entries that are not
// directories or that lack a fixture.json are silently skipped.
func (w *Writer) listCommittedFixtures() ([]FixtureInfo, error) {
	entries, err := os.ReadDir(w.cfg.SpoolDir)
	if err != nil {
		return nil, fmt.Errorf("read spool dir: %w", err)
	}

	var fixtures []FixtureInfo
	for _, entry := range entries {
		if !entry.IsDir() || IsTempDirName(entry.Name()) {
			continue
		}

		dirPath := filepath.Join(w.cfg.SpoolDir, entry.Name())
		fPath := FixtureFilePath(dirPath)

		// Only count directories that actually contain a fixture.json.
		if _, err := os.Stat(fPath); err != nil {
			continue
		}

		info, err := entry.Info()
		if err != nil {
			continue
		}

		size, err := w.dirSize(dirPath)
		if err != nil {
			continue
		}

		fixtures = append(fixtures, FixtureInfo{
			Path:      dirPath,
			FixtureID: entry.Name(),
			ModTime:   info.ModTime(),
			SizeBytes: size,
		})
	}

	return fixtures, nil
}

// deleteFixtureDir removes a committed fixture directory and returns the
// number of bytes reclaimed. The caller must hold w.mu.
func (w *Writer) deleteFixtureDir(path string) (int64, error) {
	size, err := w.dirSize(path)
	if err != nil {
		return 0, fmt.Errorf("measure dir %s: %w", path, err)
	}

	if err := os.RemoveAll(path); err != nil {
		return 0, fmt.Errorf("remove dir %s: %w", path, err)
	}

	return size, nil
}

// dirSize walks a directory tree and returns the total size of all regular
// files it contains. Used only during scan and eviction — not on the hot path.
func (w *Writer) dirSize(path string) (int64, error) {
	var total int64
	err := filepath.Walk(path, func(_ string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() {
			total += info.Size()
		}
		return nil
	})
	return total, err
}
