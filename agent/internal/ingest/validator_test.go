package ingest

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// defaultValidator returns a Validator with sensible defaults for most tests.
func defaultValidator() *Validator {
	return NewValidator(ValidatorConfig{
		MaxEventBytes:    1024,
		SupportedSchemas: []int{1, 2},
	})
}

// validEnvelope returns an Envelope that passes all validation rules.
func validEnvelope() Envelope {
	return Envelope{
		SchemaVersion: 1,
		FixtureID:     "fix-123",
		SessionID:     "sess-456",
		EventType:     EventTypeInput,
		TimestampMs:   time.Now().UnixMilli(),
		Payload:       json.RawMessage(`{"key":"value"}`),
	}
}

// --- SupportedSchema ---

func TestSupportedSchema_ReturnsTrueForConfiguredVersions(t *testing.T) {
	v := defaultValidator()
	for _, version := range []int{1, 2} {
		if !v.SupportedSchema(version) {
			t.Errorf("SupportedSchema(%d) = false, want true", version)
		}
	}
}

func TestSupportedSchema_ReturnsFalseForUnknownVersion(t *testing.T) {
	v := defaultValidator()
	if v.SupportedSchema(99) {
		t.Error("SupportedSchema(99) = true, want false")
	}
}

func TestSupportedSchema_EmptyAllowlist(t *testing.T) {
	v := NewValidator(ValidatorConfig{SupportedSchemas: []int{}})
	if v.SupportedSchema(1) {
		t.Error("SupportedSchema(1) = true on empty allowlist, want false")
	}
}

// --- ValidateID ---

func TestValidateID_ValidInputs(t *testing.T) {
	valid := []string{
		"abc",
		"ABC",
		"abc-123",
		"abc_123",
		"a",
		strings.Repeat("x", 128),
	}
	for _, id := range valid {
		if err := ValidateID(id, 128); err != nil {
			t.Errorf("ValidateID(%q) returned error %v, want nil", id, err)
		}
	}
}

func TestValidateID_EmptyIsRejected(t *testing.T) {
	if err := ValidateID("", 128); err == nil {
		t.Error("ValidateID(\"\") returned nil, want error")
	}
}

func TestValidateID_TooLongIsRejected(t *testing.T) {
	id := strings.Repeat("a", 129)
	if err := ValidateID(id, 128); err == nil {
		t.Errorf("ValidateID(len=%d) returned nil, want error", len(id))
	}
}

func TestValidateID_InvalidCharactersAreRejected(t *testing.T) {
	invalid := []string{
		"has/slash",
		`has\backslash`,
		"has.dot",
		"has space",
		"has@at",
		"has#hash",
		"has!bang",
	}
	for _, id := range invalid {
		if err := ValidateID(id, 256); err == nil {
			t.Errorf("ValidateID(%q) returned nil, want error", id)
		}
	}
}

// --- Validate: schema version ---

func TestValidate_UnsupportedSchemaVersion(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.SchemaVersion = 99

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for unsupported schema, want false")
	}
}

func TestValidate_SupportedSchemaVersion(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.SchemaVersion = 2

	r := v.Validate(&e)
	if !r.OK {
		t.Errorf("Validate() OK = false for supported schema 2: %s", r.Reason)
	}
}

// --- Validate: event type ---

func TestValidate_UnknownEventType(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.EventType = "Unknown"

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for unknown event_type, want false")
	}
}

func TestValidate_AllValidEventTypes(t *testing.T) {
	v := defaultValidator()
	types := []EventType{EventTypeInput, EventTypeStub, EventTypeOutput, EventTypeMetadata}
	for _, et := range types {
		e := validEnvelope()
		e.EventType = et
		r := v.Validate(&e)
		if !r.OK {
			t.Errorf("Validate() OK = false for EventType=%q: %s", et, r.Reason)
		}
	}
}

// --- Validate: fixture/session ID ---

func TestValidate_EmptyFixtureID(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.FixtureID = ""

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for empty fixture_id, want false")
	}
}

func TestValidate_InvalidFixtureIDChars(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.FixtureID = "bad/id"

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for fixture_id with slash, want false")
	}
}

func TestValidate_EmptySessionID(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.SessionID = ""

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for empty session_id, want false")
	}
}

func TestValidate_InvalidSessionIDChars(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.SessionID = "bad.session"

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for session_id with dot, want false")
	}
}

// --- Validate: timestamp ---

func TestValidate_ZeroTimestamp(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.TimestampMs = 0

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for zero timestamp, want false")
	}
}

func TestValidate_NegativeTimestamp(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.TimestampMs = -1

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for negative timestamp, want false")
	}
}

func TestValidate_FarFutureTimestamp(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.TimestampMs = time.Now().UnixMilli() + (10 * 60 * 1000) // 10 minutes ahead

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for far-future timestamp, want false")
	}
}

func TestValidate_FarPastTimestamp(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.TimestampMs = time.Now().UnixMilli() - (10 * 60 * 1000) // 10 minutes ago

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for far-past timestamp, want false")
	}
}

// --- Validate: payload ---

func TestValidate_EmptyPayload(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.Payload = json.RawMessage{}

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for empty payload, want false")
	}
}

func TestValidate_PayloadExceedsMaxEventBytes(t *testing.T) {
	v := NewValidator(ValidatorConfig{
		MaxEventBytes:    10,
		SupportedSchemas: []int{1},
	})
	e := validEnvelope()
	e.Payload = json.RawMessage(`{"key":"this payload is definitely longer than 10 bytes"}`)

	r := v.Validate(&e)
	if r.OK {
		t.Error("Validate() OK = true for oversized payload, want false")
	}
}

func TestValidate_PayloadAtExactLimit(t *testing.T) {
	payload := json.RawMessage(`{"k":"v"}`) // 9 bytes
	v := NewValidator(ValidatorConfig{
		MaxEventBytes:    int64(len(payload)),
		SupportedSchemas: []int{1},
	})
	e := validEnvelope()
	e.Payload = payload

	r := v.Validate(&e)
	if !r.OK {
		t.Errorf("Validate() OK = false for payload at exact limit: %s", r.Reason)
	}
}

func TestValidate_MaxEventBytesZeroMeansNoLimit(t *testing.T) {
	v := NewValidator(ValidatorConfig{
		MaxEventBytes:    0, // disabled
		SupportedSchemas: []int{1},
	})
	e := validEnvelope()
	e.Payload = json.RawMessage(`{"large":"` + strings.Repeat("x", 10000) + `"}`)

	r := v.Validate(&e)
	if !r.OK {
		t.Errorf("Validate() OK = false with MaxEventBytes=0 (no limit): %s", r.Reason)
	}
}

// --- Validate: full valid envelope ---

func TestValidate_ValidEnvelopePassesAllChecks(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()

	r := v.Validate(&e)
	if !r.OK {
		t.Errorf("Validate() OK = false for valid envelope: %s", r.Reason)
	}
	if r.Reason != "" {
		t.Errorf("Validate() Reason = %q, want empty", r.Reason)
	}
	if r.Err != nil {
		t.Errorf("Validate() Err = %v, want nil", r.Err)
	}
}

// --- Validate: ValidationResult fields on failure ---

func TestValidate_FailurePopulatesReasonAndErr(t *testing.T) {
	v := defaultValidator()
	e := validEnvelope()
	e.SchemaVersion = 99

	r := v.Validate(&e)
	if r.OK {
		t.Fatal("expected failure, got OK")
	}
	if r.Reason == "" {
		t.Error("Validate() Reason is empty on failure, want non-empty")
	}
	if r.Err == nil {
		t.Error("Validate() Err is nil on failure, want non-nil")
	}
}
