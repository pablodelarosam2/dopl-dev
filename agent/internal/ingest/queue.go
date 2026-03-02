package ingest

import (
	"context"
	"sync/atomic"
)

// Queue is a bounded, non-blocking ingest queue with an atomic depth gauge
// so health/readiness probes can read queue pressure without locking.
type Queue struct {
	ch       chan Envelope
	depth    atomic.Int64
	capacity int
}

// NewQueue creates a Queue backed by a buffered channel of the given capacity.
func NewQueue(capacity int) *Queue {
	return &Queue{
		ch:       make(chan Envelope, capacity),
		capacity: capacity,
	}
}

// TryEnqueue attempts a non-blocking send of e onto the queue.
// Returns true on success, false if the queue is full (dropped).
func (q *Queue) TryEnqueue(e Envelope) bool {
	select {
	case q.ch <- e:
		q.depth.Add(1)
		return true
	default:
		return false
	}
}

// Dequeue blocks until an Envelope is available or ctx is cancelled.
// It decrements the depth counter automatically before returning.
// Returns (envelope, true) on success, (zero, false) if ctx was cancelled.
func (q *Queue) Dequeue(ctx context.Context) (Envelope, bool) {
	select {
	case e := <-q.ch:
		q.depth.Add(-1)
		return e, true
	case <-ctx.Done():
		return Envelope{}, false
	}
}

// Chan returns the read-only channel for consumers that prefer a select loop
// and will call MarkDequeued() themselves.
func (q *Queue) Chan() <-chan Envelope {
	return q.ch
}

// MarkDequeued decrements the depth counter. Call this after receiving
// from Chan() if not using Dequeue().
func (q *Queue) MarkDequeued() {
	q.depth.Add(-1)
}

// Depth returns the current number of enqueued items not yet consumed.
func (q *Queue) Depth() int {
	return int(q.depth.Load())
}

// Capacity returns the maximum number of items the queue can hold.
func (q *Queue) Capacity() int {
	return q.capacity
}
