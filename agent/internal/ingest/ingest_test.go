package ingest

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

// --- helpers ---

// defaultIngestor returns an Ingestor wired with a standard validator and a
// queue large enough not to fill during most tests.
func defaultIngestor() *Ingestor {
	v := NewValidator(ValidatorConfig{
		MaxEventBytes:    1024,
		SupportedSchemas: []int{1},
	})
	q := NewQueue(100)
	return NewIngestor(v, q)
}

// goodEnvelope returns a single Envelope that passes all validation rules.
func goodEnvelope() Envelope {
	return Envelope{
		SchemaVersion: 1,
		FixtureID:     "fix-001",
		SessionID:     "sess-001",
		EventType:     EventTypeInput,
		TimestampMs:   time.Now().UnixMilli(),
		Payload:       json.RawMessage(`{"k":"v"}`),
	}
}

// goodRequest wraps n copies of goodEnvelope into an IngestRequest.
func goodRequest(n int) IngestRequest {
	events := make([]Envelope, n)
	for i := range events {
		events[i] = goodEnvelope()
	}
	return IngestRequest{Events: events}
}

// --- NewIngestor ---

func TestNewIngestor_QueueMetricsReflectQueue(t *testing.T) {
	v := NewValidator(ValidatorConfig{SupportedSchemas: []int{1}})
	q := NewQueue(42)
	in := NewIngestor(v, q)

	if in.QueueCapacity() != 42 {
		t.Errorf("QueueCapacity() = %d, want 42", in.QueueCapacity())
	}
	if in.QueueDepth() != 0 {
		t.Errorf("QueueDepth() = %d, want 0", in.QueueDepth())
	}
}

// --- IngestBatch: all accepted ---

func TestIngestBatch_AllValidAccepted(t *testing.T) {
	in := defaultIngestor()
	resp := in.IngestBatch(goodRequest(3))

	if resp.Accepted != 3 {
		t.Errorf("Accepted = %d, want 3", resp.Accepted)
	}
	if resp.Dropped != 0 {
		t.Errorf("Dropped = %d, want 0", resp.Dropped)
	}
	if resp.Invalid != 0 {
		t.Errorf("Invalid = %d, want 0", resp.Invalid)
	}
	if len(resp.DroppedByReason) != 0 {
		t.Errorf("DroppedByReason = %v, want empty", resp.DroppedByReason)
	}
}

func TestIngestBatch_AcceptedEventAppearsOnChannel(t *testing.T) {
	in := defaultIngestor()
	e := goodEnvelope()
	e.FixtureID = "fix-channel"
	in.IngestBatch(IngestRequest{Events: []Envelope{e}})

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	select {
	case got := <-in.Events():
		if got.FixtureID != "fix-channel" {
			t.Errorf("Events() got FixtureID=%q, want fix-channel", got.FixtureID)
		}
	case <-ctx.Done():
		t.Fatal("timed out waiting for event on Events() channel")
	}
}

// --- IngestBatch: invalid events ---

func TestIngestBatch_InvalidEventCountedCorrectly(t *testing.T) {
	in := defaultIngestor()
	bad := goodEnvelope()
	bad.FixtureID = "" // triggers ValidateID failure

	resp := in.IngestBatch(IngestRequest{Events: []Envelope{bad}})

	if resp.Accepted != 0 {
		t.Errorf("Accepted = %d, want 0", resp.Accepted)
	}
	if resp.Invalid != 1 {
		t.Errorf("Invalid = %d, want 1", resp.Invalid)
	}
	if resp.Dropped != 1 {
		t.Errorf("Dropped = %d, want 1 (invalid counts as dropped)", resp.Dropped)
	}
}

func TestIngestBatch_InvalidReasonPopulatedInMap(t *testing.T) {
	in := defaultIngestor()
	bad := goodEnvelope()
	bad.EventType = "Unknown"

	resp := in.IngestBatch(IngestRequest{Events: []Envelope{bad}})

	if len(resp.DroppedByReason) == 0 {
		t.Fatal("DroppedByReason is empty, want at least one entry")
	}
	// The reason key is the human-readable string from ValidationResult.Reason.
	for reason, count := range resp.DroppedByReason {
		if count != 1 {
			t.Errorf("DroppedByReason[%q] = %d, want 1", reason, count)
		}
	}
}

func TestIngestBatch_MultipleInvalidReasonsBucketedSeparately(t *testing.T) {
	in := defaultIngestor()

	badType := goodEnvelope()
	badType.EventType = "Unknown"

	badID := goodEnvelope()
	badID.FixtureID = "bad/id"

	resp := in.IngestBatch(IngestRequest{Events: []Envelope{badType, badID}})

	if resp.Invalid != 2 {
		t.Errorf("Invalid = %d, want 2", resp.Invalid)
	}
	if len(resp.DroppedByReason) != 2 {
		t.Errorf("DroppedByReason has %d keys, want 2: %v", len(resp.DroppedByReason), resp.DroppedByReason)
	}
}

// --- IngestBatch: queue full ---

func TestIngestBatch_QueueFullDroppedWithCorrectReason(t *testing.T) {
	v := NewValidator(ValidatorConfig{
		MaxEventBytes:    1024,
		SupportedSchemas: []int{1},
	})
	q := NewQueue(2) // tiny queue — fills after 2 events
	in := NewIngestor(v, q)

	resp := in.IngestBatch(goodRequest(5)) // 3 will overflow

	if resp.Accepted != 2 {
		t.Errorf("Accepted = %d, want 2", resp.Accepted)
	}
	if resp.Dropped != 3 {
		t.Errorf("Dropped = %d, want 3", resp.Dropped)
	}
	if resp.DroppedByReason["queue_full"] != 3 {
		t.Errorf("DroppedByReason[queue_full] = %d, want 3", resp.DroppedByReason["queue_full"])
	}
	if resp.Invalid != 0 {
		t.Errorf("Invalid = %d, want 0 (queue_full is not an invalid event)", resp.Invalid)
	}
}

// --- IngestBatch: mixed batch ---

func TestIngestBatch_MixedBatchCountsAllOutcomes(t *testing.T) {
	v := NewValidator(ValidatorConfig{
		MaxEventBytes:    1024,
		SupportedSchemas: []int{1},
	})
	q := NewQueue(2) // accepts 2
	in := NewIngestor(v, q)

	events := []Envelope{
		goodEnvelope(),         // accepted
		goodEnvelope(),         // accepted (queue now full)
		goodEnvelope(),         // dropped: queue_full
		func() Envelope {       // dropped: invalid
			e := goodEnvelope()
			e.FixtureID = ""
			return e
		}(),
	}

	resp := in.IngestBatch(IngestRequest{Events: events})

	if resp.Accepted != 2 {
		t.Errorf("Accepted = %d, want 2", resp.Accepted)
	}
	if resp.Dropped != 2 {
		t.Errorf("Dropped = %d, want 2", resp.Dropped)
	}
	if resp.Invalid != 1 {
		t.Errorf("Invalid = %d, want 1", resp.Invalid)
	}
	if resp.DroppedByReason["queue_full"] != 1 {
		t.Errorf("DroppedByReason[queue_full] = %d, want 1", resp.DroppedByReason["queue_full"])
	}
}

// --- IngestBatch: empty request ---

func TestIngestBatch_EmptyRequestReturnsZeroes(t *testing.T) {
	in := defaultIngestor()
	resp := in.IngestBatch(IngestRequest{Events: []Envelope{}})

	if resp.Accepted != 0 || resp.Dropped != 0 || resp.Invalid != 0 {
		t.Errorf("expected all-zero response for empty request, got %+v", resp)
	}
}

// --- Events / QueueDepth / QueueCapacity ---

func TestQueueDepth_IncreasesAfterIngestBatch(t *testing.T) {
	in := defaultIngestor()
	in.IngestBatch(goodRequest(3))

	if in.QueueDepth() != 3 {
		t.Errorf("QueueDepth() = %d, want 3", in.QueueDepth())
	}
}

func TestQueueDepth_DecreasesAfterConsume(t *testing.T) {
	in := defaultIngestor()
	in.IngestBatch(goodRequest(2))

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	<-in.Events()
	in.queue.MarkDequeued()

	if in.QueueDepth() != 1 {
		t.Errorf("QueueDepth() = %d after one consume, want 1", in.QueueDepth())
	}
	_ = cancel
}

func TestQueueCapacity_IsStable(t *testing.T) {
	v := NewValidator(ValidatorConfig{SupportedSchemas: []int{1}})
	q := NewQueue(50)
	in := NewIngestor(v, q)

	in.IngestBatch(goodRequest(10))

	if in.QueueCapacity() != 50 {
		t.Errorf("QueueCapacity() = %d after enqueues, want 50", in.QueueCapacity())
	}
}

// --- ShouldLogDrop ---

func TestShouldLogDrop_TrueOnFirstCall(t *testing.T) {
	in := defaultIngestor()

	if !in.ShouldLogDrop("queue_full") {
		t.Error("ShouldLogDrop() = false on first call, want true")
	}
}

func TestShouldLogDrop_FalseOnImmediateRepeat(t *testing.T) {
	in := defaultIngestor()
	in.ShouldLogDrop("queue_full") // prime it

	if in.ShouldLogDrop("queue_full") {
		t.Error("ShouldLogDrop() = true on immediate repeat, want false (cooldown active)")
	}
}

func TestShouldLogDrop_DifferentReasonsAreIndependent(t *testing.T) {
	in := defaultIngestor()
	in.ShouldLogDrop("queue_full") // suppress queue_full

	// A different reason should still be allowed.
	if !in.ShouldLogDrop("invalid_schema") {
		t.Error("ShouldLogDrop(invalid_schema) = false, want true (independent cooldown)")
	}
}

func TestShouldLogDrop_TrueAfterCooldownExpires(t *testing.T) {
	in := defaultIngestor()
	// Manually backdate the last-log time to simulate cooldown expiry.
	in.dropLogMu.Lock()
	in.dropLogAt["queue_full"] = time.Now().Add(-dropLogCooldown - time.Millisecond)
	in.dropLogMu.Unlock()

	if !in.ShouldLogDrop("queue_full") {
		t.Error("ShouldLogDrop() = false after cooldown expired, want true")
	}
}
