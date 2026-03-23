package uploader

import (
	"bytes"
	"context"
	"encoding/json"
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

	// Build S3 key: try structured key from fixture metadata, fall back to flat.
	key := u.buildKeyFromFixture(data, fi.FixtureID)

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

// fixtureMetadata is the subset of fixture JSON fields used for S3 key construction.
type fixtureMetadata struct {
	Service      json.RawMessage `json:"service"`
	CreatedAtMs  int64           `json:"created_at_ms"`
	GoldenOutput json.RawMessage `json:"golden_output"`
}

// goldenOutputMeta extracts method/path from the golden_output payload.
type goldenOutputMeta struct {
	Method string `json:"method"`
	Path   string `json:"path"`
}

// buildKeyFromFixture parses the fixture JSON to extract metadata for structured
// key generation. Falls back to the legacy flat key if metadata is incomplete.
func (u *Uploader) buildKeyFromFixture(data []byte, fixtureID string) string {
	var meta fixtureMetadata
	if err := json.Unmarshal(data, &meta); err != nil {
		u.log.Debug("cannot parse fixture metadata, using flat key",
			"fixture_id", fixtureID, "error", err)
		return s3Key(u.cfg.Prefix, fixtureID)
	}

	// Extract service name (stored as a JSON string in RawMessage).
	var service string
	if len(meta.Service) > 0 {
		if err := json.Unmarshal(meta.Service, &service); err != nil {
			u.log.Debug("cannot parse service field, using flat key",
				"fixture_id", fixtureID, "error", err)
			return s3Key(u.cfg.Prefix, fixtureID)
		}
	}

	// Extract method/path from golden_output payload.
	var output goldenOutputMeta
	if len(meta.GoldenOutput) > 0 {
		if err := json.Unmarshal(meta.GoldenOutput, &output); err != nil {
			u.log.Debug("cannot parse golden_output metadata, using flat key",
				"fixture_id", fixtureID, "error", err)
			return s3Key(u.cfg.Prefix, fixtureID)
		}
	}

	createdAt := time.UnixMilli(meta.CreatedAtMs)

	return structuredS3Key(service, output.Method, output.Path, fixtureID, createdAt)
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
