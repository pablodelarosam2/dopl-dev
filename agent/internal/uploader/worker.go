package uploader

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"time"

	"github.com/dopl-dev/agent/internal/spool"
)

// worker reads jobs from the shared channel and processes each fixture.
// It returns when the jobs channel is closed (during shutdown).
func (u *Uploader) worker(ctx context.Context, id int) {
	defer u.wg.Done()

	for job := range u.jobs {
		u.processFixture(ctx, job.info)
	}
}

// processFixture reads a fixture from disk, uploads it to S3 with retries,
// and removes the local copy on success.
func (u *Uploader) processFixture(ctx context.Context, fi spool.FixtureInfo) {
	defer u.clearInFlight(fi.FixtureID)

	// Read fixture.json from the committed fixture directory.
	filePath := spool.FixtureFilePath(fi.Path)
	data, err := os.ReadFile(filePath)
	if err != nil {
		if os.IsNotExist(err) {
			// Fixture was evicted by spool LRU between scan and processing.
			// Not an error — just skip.
			u.log.Debug("fixture vanished before upload", "fixture_id", fi.FixtureID)
			return
		}
		u.log.Error("failed to read fixture for upload",
			"fixture_id", fi.FixtureID,
			"path", filePath,
			"error", err,
		)
		u.setLastError("read: " + err.Error())
		u.incrFailed()
		return
	}

	// Upload to S3 with retries.
	key := s3Key(u.cfg.Prefix, fi.FixtureID)
	err = u.uploadWithRetry(ctx, key, data)
	if err != nil {
		u.log.Error("upload failed after retries",
			"fixture_id", fi.FixtureID,
			"key", key,
			"error", err,
		)
		u.setLastError("upload: " + err.Error())
		u.incrFailed()
		return
	}

	// Upload succeeded — delete the local fixture directory.
	if err := os.RemoveAll(fi.Path); err != nil {
		// Log but do not count as failure — the data is safely in S3.
		// The spool eviction will eventually clean it up anyway.
		u.log.Warn("failed to remove uploaded fixture from spool",
			"fixture_id", fi.FixtureID,
			"path", fi.Path,
			"error", err,
		)
	}

	u.incrCompleted(int64(len(data)))
	u.clearLastError()

	u.log.Debug("fixture uploaded and cleaned up",
		"fixture_id", fi.FixtureID,
		"key", key,
		"bytes", len(data),
	)
}

// uploadWithRetry attempts PutObject up to MaxRetries times with exponential
// backoff. It returns nil on success, or the last error after all retries are
// exhausted.
func (u *Uploader) uploadWithRetry(ctx context.Context, key string, data []byte) error {
	var lastErr error

	for attempt := 0; attempt < u.cfg.MaxRetries; attempt++ {
		if ctx.Err() != nil {
			return ctx.Err()
		}

		err := u.client.PutObject(ctx, &PutObjectInput{
			Key:         key,
			Body:        bytes.NewReader(data),
			ContentType: "application/json",
		})
		if err == nil {
			return nil
		}

		lastErr = err
		u.log.Warn("upload attempt failed, will retry",
			"key", key,
			"attempt", attempt+1,
			"max_retries", u.cfg.MaxRetries,
			"error", err,
		)

		// Wait with exponential backoff before retrying, unless this was
		// the last attempt.
		if attempt < u.cfg.MaxRetries-1 {
			delay := backoffDuration(attempt)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(delay):
			}
		}
	}

	return lastErr
}

// backoffDuration returns the wait duration for the given retry attempt using
// exponential backoff: 500ms * 2^attempt, capped at 30s.
func backoffDuration(attempt int) time.Duration {
	base := 500 * time.Millisecond
	delay := base << uint(attempt) // 500ms, 1s, 2s, 4s, ...
	cap := 30 * time.Second
	if delay > cap {
		return cap
	}
	return delay
}

// s3Key builds the full S3 object key for a fixture.
// Format: {prefix}/{fixtureID}/fixture.json (prefix may be empty).
func s3Key(prefix, fixtureID string) string {
	if prefix == "" {
		return fixtureID + "/fixture.json"
	}
	return filepath.ToSlash(filepath.Join(prefix, fixtureID, "fixture.json"))
}
