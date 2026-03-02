package ingest

import (
	"fmt"
	"regexp"
	"time"
)

// idCharset matches the allowlist: alphanumerics, hyphens, and underscores only.
var idCharset = regexp.MustCompile(`^[a-zA-Z0-9_-]+$`)

// maxTimestampDriftMs is the maximum allowed drift from now before a timestamp
// is considered obviously wrong (5 minutes).
const maxTimestampDriftMs = 5 * 60 * 1000

const maxIDLen = 128

// ValidatorConfig holds the limits and supported schema versions for the validator.
type ValidatorConfig struct {
	MaxEventBytes    int64
	SupportedSchemas []int
}

// Validator validates individual Envelope events against configured limits.
type Validator struct {
	cfg            ValidatorConfig
	supportedSet   map[int]struct{}
}

// ValidationResult is the outcome of validating a single Envelope.
type ValidationResult struct {
	OK     bool
	Reason string
	Err    error
}

func ok() ValidationResult {
	return ValidationResult{OK: true}
}

func fail(reason string, err error) ValidationResult {
	return ValidationResult{OK: false, Reason: reason, Err: err}
}

// NewValidator constructs a Validator from the given config.
func NewValidator(cfg ValidatorConfig) *Validator {
	set := make(map[int]struct{}, len(cfg.SupportedSchemas))
	for _, v := range cfg.SupportedSchemas {
		set[v] = struct{}{}
	}
	return &Validator{cfg: cfg, supportedSet: set}
}

// SupportedSchema reports whether version is in the configured schema allowlist.
func (v *Validator) SupportedSchema(version int) bool {
	_, ok := v.supportedSet[version]
	return ok
}

// ValidateID enforces the allowlist charset (alphanumeric, hyphen, underscore),
// rejects path separators (/ \) and dots, and enforces maxLen.
func ValidateID(id string, maxLen int) error {
	if id == "" {
		return fmt.Errorf("id must not be empty")
	}
	if len(id) > maxLen {
		return fmt.Errorf("id length %d exceeds max %d", len(id), maxLen)
	}
	if !idCharset.MatchString(id) {
		return fmt.Errorf("id %q contains invalid characters (allowed: a-z A-Z 0-9 _ -)", id)
	}
	return nil
}

// Validate checks a single Envelope against all configured rules and returns
// a ValidationResult describing whether it passed and why it failed if not.
func (v *Validator) Validate(e *Envelope) ValidationResult {
	if !v.SupportedSchema(e.SchemaVersion) {
		return fail(
			fmt.Sprintf("unsupported schema version %d", e.SchemaVersion),
			fmt.Errorf("unsupported schema version: %d", e.SchemaVersion),
		)
	}

	if !e.EventType.Valid() {
		return fail(
			fmt.Sprintf("unknown event_type %q", e.EventType),
			fmt.Errorf("unknown event_type: %q", e.EventType),
		)
	}

	if err := ValidateID(e.FixtureID, maxIDLen); err != nil {
		return fail(fmt.Sprintf("invalid fixture_id: %s", err), err)
	}

	if err := ValidateID(e.SessionID, maxIDLen); err != nil {
		return fail(fmt.Sprintf("invalid session_id: %s", err), err)
	}

	if err := v.validateTimestamp(e.TimestampMs); err != nil {
		return fail(fmt.Sprintf("invalid timestamp: %s", err), err)
	}

	if len(e.Payload) == 0 {
		return fail("payload must not be empty", fmt.Errorf("payload is empty"))
	}

	if v.cfg.MaxEventBytes > 0 && int64(len(e.Payload)) > v.cfg.MaxEventBytes {
		return fail(
			fmt.Sprintf("payload size %d exceeds max %d bytes", len(e.Payload), v.cfg.MaxEventBytes),
			fmt.Errorf("payload size %d exceeds max %d bytes", len(e.Payload), v.cfg.MaxEventBytes),
		)
	}

	return ok()
}

// validateTimestamp rejects timestamps that are zero or obviously wrong
// (more than maxTimestampDriftMs milliseconds from now).
func (v *Validator) validateTimestamp(tsMs int64) error {
	if tsMs <= 0 {
		return fmt.Errorf("timestamp must be a positive Unix millisecond value")
	}
	nowMs := time.Now().UnixMilli()
	drift := tsMs - nowMs
	if drift < 0 {
		drift = -drift
	}
	if drift > maxTimestampDriftMs {
		return fmt.Errorf("timestamp drift %dms exceeds allowed %dms", drift, maxTimestampDriftMs)
	}
	return nil
}
