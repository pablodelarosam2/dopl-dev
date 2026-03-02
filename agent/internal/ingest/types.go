package ingest

import (
	"encoding/json"
	"errors"
	"fmt"
)

type EventType string
const (
	EventTypeInput EventType = "Input"
	EventTypeStub EventType = "Stub"
	EventTypeOutput EventType = "Output"
	EventTypeMetadata EventType = "Metadata"
)
type Envelope struct {
	SchemaVersion int
	FixtureID string
	SessionID string
	EventType EventType
	TimestampMs int64
	Payload json.RawMessage
	Service string
	Trace string
}
type IngestRequest struct {
	Events []Envelope
}
type IngestResponse struct {
	Accepted int
	Dropped int
	DroppedByReason map[string]int
	Invalid int // optional — invalid schema
}
func (t EventType) Valid() bool {
	return t == EventTypeInput || t == EventTypeStub || t == EventTypeOutput || t == EventTypeMetadata
}

// Validate checks that the IngestRequest is well-formed.
func (r *IngestRequest) Validate() error {
	if len(r.Events) == 0 {
		return errors.New("events must not be empty")
	}
	for i, e := range r.Events {
		if e.FixtureID == "" {
			return fmt.Errorf("event[%d]: fixture_id must not be empty", i)
		}
		if e.SessionID == "" {
			return fmt.Errorf("event[%d]: session_id must not be empty", i)
		}
		if !e.EventType.Valid() {
			return fmt.Errorf("event[%d]: unknown event_type %q", i, e.EventType)
		}
	}
	return nil
}
