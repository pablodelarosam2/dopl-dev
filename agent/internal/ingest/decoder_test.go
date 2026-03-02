package ingest

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// validJSON is a minimal IngestRequest body that passes all validation rules.
const validJSON = `{
	"Events": [{
		"SchemaVersion": 1,
		"FixtureID": "fix-001",
		"SessionID": "sess-001",
		"EventType": "Input",
		"TimestampMs": 9999999999999,
		"Payload": {"key":"value"}
	}]
}`

// reader wraps a string as an io.Reader — shorter than strings.NewReader inline.
func reader(s string) io.Reader {
	return strings.NewReader(s)
}

// --- BodyTooLargeError ---

func TestBodyTooLargeError_Message(t *testing.T) {
	err := &BodyTooLargeError{Max: 1024}
	want := "request body exceeds limit of 1024 bytes"
	if err.Error() != want {
		t.Errorf("Error() = %q, want %q", err.Error(), want)
	}
}

// --- DecodeRequest: valid input ---

func TestDecodeRequest_ValidBodyReturnsRequest(t *testing.T) {
	req, err := DecodeRequest(reader(validJSON))
	if err != nil {
		t.Fatalf("DecodeRequest() error = %v, want nil", err)
	}
	if len(req.Events) != 1 {
		t.Fatalf("len(Events) = %d, want 1", len(req.Events))
	}
	e := req.Events[0]
	if e.FixtureID != "fix-001" {
		t.Errorf("FixtureID = %q, want fix-001", e.FixtureID)
	}
	if e.SessionID != "sess-001" {
		t.Errorf("SessionID = %q, want sess-001", e.SessionID)
	}
	if e.EventType != EventTypeInput {
		t.Errorf("EventType = %q, want Input", e.EventType)
	}
}

func TestDecodeRequest_AllEventTypesAccepted(t *testing.T) {
	types := []string{"Input", "Stub", "Output", "Metadata"}
	for _, et := range types {
		body := fmt.Sprintf(`{"Events":[{"FixtureID":"f","SessionID":"s","EventType":%q,"TimestampMs":9999999999999,"Payload":{}}]}`, et)
		if _, err := DecodeRequest(reader(body)); err != nil {
			t.Errorf("EventType=%q: DecodeRequest() error = %v, want nil", et, err)
		}
	}
}

// --- DecodeRequest: malformed JSON ---

func TestDecodeRequest_EmptyBodyReturnsErrInvalidJSON(t *testing.T) {
	_, err := DecodeRequest(reader(""))
	if err == nil {
		t.Fatal("DecodeRequest(\"\") returned nil error, want ErrInvalidJSON")
	}
	if !errors.Is(err, ErrInvalidJSON) {
		t.Errorf("errors.Is(err, ErrInvalidJSON) = false, got %v", err)
	}
}

func TestDecodeRequest_NotJSONReturnsErrInvalidJSON(t *testing.T) {
	_, err := DecodeRequest(reader("not json at all"))
	if !errors.Is(err, ErrInvalidJSON) {
		t.Errorf("errors.Is(err, ErrInvalidJSON) = false, got %v", err)
	}
}

func TestDecodeRequest_TruncatedJSONReturnsErrInvalidJSON(t *testing.T) {
	_, err := DecodeRequest(reader(`{"Events":[{`))
	if !errors.Is(err, ErrInvalidJSON) {
		t.Errorf("errors.Is(err, ErrInvalidJSON) = false, got %v", err)
	}
}

// --- DecodeRequest: unknown fields ---

func TestDecodeRequest_UnknownTopLevelFieldIsRejected(t *testing.T) {
	body := `{"Events":[],"UnknownField":"oops"}`
	_, err := DecodeRequest(reader(body))
	if err == nil {
		t.Error("DecodeRequest() returned nil for body with unknown field, want error")
	}
	if !errors.Is(err, ErrInvalidJSON) {
		t.Errorf("errors.Is(err, ErrInvalidJSON) = false, got %v", err)
	}
}

// --- DecodeRequest: validation failures ---

func TestDecodeRequest_EmptyEventsArrayIsRejected(t *testing.T) {
	body := `{"Events":[]}`
	_, err := DecodeRequest(reader(body))
	if err == nil {
		t.Error("DecodeRequest() returned nil for empty Events array, want error")
	}
	// This is a validation error, not a JSON error.
	if errors.Is(err, ErrInvalidJSON) {
		t.Error("expected validation error, not ErrInvalidJSON")
	}
}

func TestDecodeRequest_MissingFixtureIDIsRejected(t *testing.T) {
	body := `{"Events":[{"SessionID":"s","EventType":"Input","TimestampMs":9999999999999,"Payload":{}}]}`
	_, err := DecodeRequest(reader(body))
	if err == nil {
		t.Error("DecodeRequest() returned nil for missing FixtureID, want error")
	}
}

func TestDecodeRequest_MissingSessionIDIsRejected(t *testing.T) {
	body := `{"Events":[{"FixtureID":"f","EventType":"Input","TimestampMs":9999999999999,"Payload":{}}]}`
	_, err := DecodeRequest(reader(body))
	if err == nil {
		t.Error("DecodeRequest() returned nil for missing SessionID, want error")
	}
}

func TestDecodeRequest_InvalidEventTypeIsRejected(t *testing.T) {
	body := `{"Events":[{"FixtureID":"f","SessionID":"s","EventType":"Bad","TimestampMs":9999999999999,"Payload":{}}]}`
	_, err := DecodeRequest(reader(body))
	if err == nil {
		t.Error("DecodeRequest() returned nil for invalid EventType, want error")
	}
}

// --- DecodeRequestFromHTTP: valid ---

func TestDecodeRequestFromHTTP_ValidRequestSucceeds(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/ingest", reader(validJSON))
	w := httptest.NewRecorder()

	req, err := DecodeRequestFromHTTP(r, w, 1024*1024)
	if err != nil {
		t.Fatalf("DecodeRequestFromHTTP() error = %v, want nil", err)
	}
	if len(req.Events) != 1 {
		t.Errorf("len(Events) = %d, want 1", len(req.Events))
	}
}

// --- DecodeRequestFromHTTP: oversized body ---

func TestDecodeRequestFromHTTP_OversizedBodyReturnsBodyTooLargeError(t *testing.T) {
	// Build a body that is definitely larger than the 10-byte limit.
	body := strings.Repeat("x", 100)
	r := httptest.NewRequest(http.MethodPost, "/ingest", strings.NewReader(body))
	w := httptest.NewRecorder()

	_, err := DecodeRequestFromHTTP(r, w, 10)
	if err == nil {
		t.Fatal("DecodeRequestFromHTTP() returned nil for oversized body, want error")
	}

	var tooLarge *BodyTooLargeError
	if !errors.As(err, &tooLarge) {
		t.Errorf("errors.As(err, *BodyTooLargeError) = false, got %T: %v", err, err)
	}
	if tooLarge.Max != 10 {
		t.Errorf("BodyTooLargeError.Max = %d, want 10", tooLarge.Max)
	}
}

func TestDecodeRequestFromHTTP_BodyAtExactLimitIsAccepted(t *testing.T) {
	body := validJSON
	r := httptest.NewRequest(http.MethodPost, "/ingest", reader(body))
	w := httptest.NewRecorder()

	_, err := DecodeRequestFromHTTP(r, w, int64(len(body)))
	if err != nil {
		t.Errorf("DecodeRequestFromHTTP() error = %v for body at exact limit, want nil", err)
	}
}

// --- DecodeRequestFromHTTP: invalid JSON via HTTP ---

func TestDecodeRequestFromHTTP_InvalidJSONReturnsErrInvalidJSON(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/ingest", reader("not json"))
	w := httptest.NewRecorder()

	_, err := DecodeRequestFromHTTP(r, w, 1024*1024)
	if err == nil {
		t.Fatal("DecodeRequestFromHTTP() returned nil for invalid JSON, want error")
	}
	if !errors.Is(err, ErrInvalidJSON) {
		t.Errorf("errors.Is(err, ErrInvalidJSON) = false, got %v", err)
	}
}

// --- ErrInvalidJSON wrapping ---

func TestDecodeRequest_ErrorWrapsErrInvalidJSON(t *testing.T) {
	_, err := DecodeRequest(reader("{}"))
	// {} has no Events field — DecodeRequest will call Validate(), which returns
	// a plain error, not ErrInvalidJSON. Confirm the two error paths are distinct.
	if errors.Is(err, ErrInvalidJSON) {
		t.Error("validation error should NOT be wrapped as ErrInvalidJSON")
	}
}
