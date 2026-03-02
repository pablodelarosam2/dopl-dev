package ingest

import (
	"context"
	"testing"
	"time"
)

func envelope(fixtureID string) Envelope {
	return Envelope{FixtureID: fixtureID}
}

// --- NewQueue ---

func TestNewQueue_CapacityAndInitialDepth(t *testing.T) {
	q := NewQueue(10)

	if q.Capacity() != 10 {
		t.Errorf("Capacity() = %d, want 10", q.Capacity())
	}
	if q.Depth() != 0 {
		t.Errorf("Depth() = %d, want 0", q.Depth())
	}
}

// --- TryEnqueue ---

func TestTryEnqueue_SuccessIncrementsDepth(t *testing.T) {
	q := NewQueue(5)

	if !q.TryEnqueue(envelope("fix-1")) {
		t.Fatal("TryEnqueue() returned false, want true")
	}
	if q.Depth() != 1 {
		t.Errorf("Depth() = %d, want 1", q.Depth())
	}
}

func TestTryEnqueue_FillsToCapacity(t *testing.T) {
	q := NewQueue(3)

	for i := 0; i < 3; i++ {
		if !q.TryEnqueue(envelope("fix")) {
			t.Fatalf("TryEnqueue() returned false at i=%d, want true", i)
		}
	}
	if q.Depth() != 3 {
		t.Errorf("Depth() = %d, want 3", q.Depth())
	}
}

func TestTryEnqueue_ReturnsFalseWhenFull(t *testing.T) {
	q := NewQueue(2)
	q.TryEnqueue(envelope("a"))
	q.TryEnqueue(envelope("b"))

	if q.TryEnqueue(envelope("c")) {
		t.Error("TryEnqueue() returned true on full queue, want false")
	}
	if q.Depth() != 2 {
		t.Errorf("Depth() = %d, want 2 (dropped item must not increment depth)", q.Depth())
	}
}

func TestTryEnqueue_PreservesEnvelopeData(t *testing.T) {
	q := NewQueue(1)
	e := Envelope{FixtureID: "fix-abc", SessionID: "sess-xyz"}
	q.TryEnqueue(e)

	got := <-q.Chan()
	if got.FixtureID != "fix-abc" || got.SessionID != "sess-xyz" {
		t.Errorf("got envelope %+v, want FixtureID=fix-abc SessionID=sess-xyz", got)
	}
}

// --- Dequeue ---

func TestDequeue_ReturnsEnqueuedEnvelope(t *testing.T) {
	q := NewQueue(1)
	want := Envelope{FixtureID: "fix-1", SessionID: "sess-1"}
	q.TryEnqueue(want)

	got, ok := q.Dequeue(context.Background())
	if !ok {
		t.Fatal("Dequeue() returned false, want true")
	}
	if got.FixtureID != want.FixtureID || got.SessionID != want.SessionID {
		t.Errorf("Dequeue() got %+v, want %+v", got, want)
	}
}

func TestDequeue_DecrementsDepth(t *testing.T) {
	q := NewQueue(3)
	q.TryEnqueue(envelope("a"))
	q.TryEnqueue(envelope("b"))

	q.Dequeue(context.Background())
	if q.Depth() != 1 {
		t.Errorf("Depth() = %d after one dequeue, want 1", q.Depth())
	}
}

func TestDequeue_CancelledContextReturnsFalse(t *testing.T) {
	q := NewQueue(5)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, ok := q.Dequeue(ctx)
	if ok {
		t.Error("Dequeue() returned true on cancelled context, want false")
	}
}

func TestDequeue_BlocksUntilEnqueue(t *testing.T) {
	q := NewQueue(1)

	go func() {
		time.Sleep(20 * time.Millisecond)
		q.TryEnqueue(envelope("delayed"))
	}()

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	e, ok := q.Dequeue(ctx)
	if !ok {
		t.Fatal("Dequeue() returned false, want true")
	}
	if e.FixtureID != "delayed" {
		t.Errorf("Dequeue() got FixtureID=%q, want delayed", e.FixtureID)
	}
}

// --- Chan + MarkDequeued ---

func TestChan_ReceiveAndMarkDequeued(t *testing.T) {
	q := NewQueue(2)
	q.TryEnqueue(envelope("a"))
	q.TryEnqueue(envelope("b"))

	<-q.Chan()
	q.MarkDequeued()

	if q.Depth() != 1 {
		t.Errorf("Depth() = %d after Chan receive + MarkDequeued, want 1", q.Depth())
	}
}

func TestMarkDequeued_WithoutChanReadDecrements(t *testing.T) {
	q := NewQueue(3)
	q.TryEnqueue(envelope("x"))
	q.TryEnqueue(envelope("y"))

	<-q.Chan()
	q.MarkDequeued()
	<-q.Chan()
	q.MarkDequeued()

	if q.Depth() != 0 {
		t.Errorf("Depth() = %d, want 0", q.Depth())
	}
}

// --- Depth / Capacity ---

func TestDepth_TracksEnqueueDequeue(t *testing.T) {
	q := NewQueue(10)

	for i := 0; i < 5; i++ {
		q.TryEnqueue(envelope("e"))
	}
	if q.Depth() != 5 {
		t.Errorf("Depth() = %d after 5 enqueues, want 5", q.Depth())
	}

	q.Dequeue(context.Background())
	q.Dequeue(context.Background())
	if q.Depth() != 3 {
		t.Errorf("Depth() = %d after 2 dequeues, want 3", q.Depth())
	}
}

func TestCapacity_IsImmutable(t *testing.T) {
	q := NewQueue(7)
	q.TryEnqueue(envelope("a"))
	q.TryEnqueue(envelope("b"))

	if q.Capacity() != 7 {
		t.Errorf("Capacity() = %d after enqueues, want 7", q.Capacity())
	}
}
