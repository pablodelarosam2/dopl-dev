package spool

import (
	"encoding/json"
	"fmt"
	"regexp"
	"time"
)

// idCharset matches the allowlist for fixture/session IDs: alphanumerics,
// hyphens, and underscores only. This is intentionally a local copy of the
// same pattern used in the ingest validator so spool has no cross-package
// dependency on ingest.
var idCharset = regexp.MustCompile(`^[a-zA-Z0-9_-]+$`)

// maxIDLen is the upper bound on fixture and session ID length.
const maxIDLen = 128

// SpoolConfig holds the settings needed by the spool Writer.
type SpoolConfig struct {
	// SpoolDir is the root directory where committed fixture directories live.
	SpoolDir string

	// MaxSpoolBytes is the hard cap on total committed fixture bytes on disk.
	// When exceeded, the oldest fixtures are evicted to make room.
	MaxSpoolBytes int64
}

// Validate checks that the config is usable.
func (c SpoolConfig) Validate() error {
	if c.SpoolDir == "" {
		return fmt.Errorf("spool directory must not be empty")
	}
	if c.MaxSpoolBytes <= 0 {
		return fmt.Errorf("max spool bytes must be > 0")
	}
	return nil
}

// FixtureBundle is the session-level artifact that gets serialized to a single
// fixture.json file on disk. Payload fields use json.RawMessage so the spool
// layer never re-parses data that was already validated/serialized by ingest.
type FixtureBundle struct {
	SchemaVersion int              `json:"schema_version"`
	FixtureID     string           `json:"fixture_id"`
	SessionID     string           `json:"session_id"`
	CreatedAtMs   int64            `json:"created_at_ms"`
	Service       json.RawMessage  `json:"service,omitempty"`
	Input         json.RawMessage  `json:"input,omitempty"`
	Stubs         json.RawMessage  `json:"stubs,omitempty"`
	GoldenOutput  json.RawMessage  `json:"golden_output,omitempty"`
	Metadata      json.RawMessage  `json:"metadata,omitempty"`
}

// ValidateBasic checks that the bundle carries the minimum required fields
// before the spool layer attempts to write it. It does not deep-validate
// the payload contents — that is the ingest layer's job.
func (b FixtureBundle) ValidateBasic() error {
	if b.SchemaVersion <= 0 {
		return fmt.Errorf("schema_version must be > 0, got %d", b.SchemaVersion)
	}
	if err := validateID(b.FixtureID, "fixture_id"); err != nil {
		return err
	}
	if err := validateID(b.SessionID, "session_id"); err != nil {
		return err
	}
	if b.CreatedAtMs <= 0 {
		return fmt.Errorf("created_at_ms must be > 0")
	}
	return nil
}

// validateID enforces the safe-for-path allowlist on an identifier.
func validateID(id, fieldName string) error {
	if id == "" {
		return fmt.Errorf("%s must not be empty", fieldName)
	}
	if len(id) > maxIDLen {
		return fmt.Errorf("%s length %d exceeds max %d", fieldName, len(id), maxIDLen)
	}
	if !idCharset.MatchString(id) {
		return fmt.Errorf("%s %q contains invalid characters (allowed: a-z A-Z 0-9 _ -)", fieldName, id)
	}
	return nil
}

// WriteResult reports the outcome of a single WriteFixture call.
type WriteResult struct {
	// BytesWritten is the number of bytes committed to disk (0 if dropped).
	BytesWritten int64

	// Dropped is true when the fixture could not be persisted (e.g. spool full).
	Dropped bool

	// DropReason is a human-readable reason when Dropped is true.
	DropReason string
}

// FixtureInfo describes a committed fixture directory on disk.
// Used by cleanup and capacity enforcement to sort and evict fixtures.
type FixtureInfo struct {
	Path      string
	FixtureID string
	ModTime   time.Time
	SizeBytes int64
}
