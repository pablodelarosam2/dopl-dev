package uploader

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/dopl-dev/agent/internal/spool"
)

// ─── Mock S3 Client ─────────────────────────────────────────────────────────

type mockS3Client struct {
	mu       sync.Mutex
	calls    []putCall
	err      error       // if non-nil, every call returns this
	failN    int         // fail the first N calls, then succeed
	callCount atomic.Int64
}

type putCall struct {
	Key         string
	ContentType string
	Body        []byte
}

func (m *mockS3Client) PutObject(_ context.Context, params *PutObjectInput) error {
	n := m.callCount.Add(1)

	body, _ := io.ReadAll(params.Body)
	m.mu.Lock()
	m.calls = append(m.calls, putCall{
		Key:         params.Key,
		ContentType: params.ContentType,
		Body:        body,
	})
	failN := m.failN
	err := m.err
	m.mu.Unlock()

	if failN > 0 && int(n) <= failN {
		if err != nil {
			return err
		}
		return errors.New("mock: transient failure")
	}
	if failN == 0 && err != nil {
		return err
	}
	return nil
}

func (m *mockS3Client) getCalls() []putCall {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]putCall, len(m.calls))
	copy(out, m.calls)
	return out
}

// ─── Mock Spool Lister ──────────────────────────────────────────────────────

type mockSpoolLister struct {
	mu       sync.Mutex
	fixtures []spool.FixtureInfo
	err      error
}

func (m *mockSpoolLister) List() ([]spool.FixtureInfo, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.err != nil {
		return nil, m.err
	}
	out := make([]spool.FixtureInfo, len(m.fixtures))
	copy(out, m.fixtures)
	return out, nil
}

func (m *mockSpoolLister) setFixtures(fixtures []spool.FixtureInfo) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.fixtures = fixtures
}

// ─── Helpers ────────────────────────────────────────────────────────────────

func testLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func testConfig() UploaderConfig {
	return UploaderConfig{
		Bucket:       "test-bucket",
		Region:       "us-east-1",
		Prefix:       "fixtures",
		Workers:      2,
		ScanInterval: 50 * time.Millisecond,
		MaxRetries:   3,
	}
}

// writeFixtureFile creates a committed fixture directory with a fixture.json.
func writeFixtureFile(t *testing.T, dir, fixtureID string, payload map[string]any) spool.FixtureInfo {
	t.Helper()
	fixtureDir := filepath.Join(dir, fixtureID)
	if err := os.MkdirAll(fixtureDir, 0o755); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	filePath := filepath.Join(fixtureDir, "fixture.json")
	if err := os.WriteFile(filePath, data, 0o644); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(fixtureDir)
	if err != nil {
		t.Fatal(err)
	}
	return spool.FixtureInfo{
		Path:      fixtureDir,
		FixtureID: fixtureID,
		ModTime:   info.ModTime(),
		SizeBytes: int64(len(data)),
	}
}

// ─── Tests ──────────────────────────────────────────────────────────────────

func TestNewValidatesConfig(t *testing.T) {
	_, err := New(&mockS3Client{}, &mockSpoolLister{}, UploaderConfig{}, testLogger())
	if err == nil {
		t.Fatal("expected validation error for empty config")
	}
}

func TestNewSuccess(t *testing.T) {
	u, err := New(&mockS3Client{}, &mockSpoolLister{}, testConfig(), testLogger())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if u == nil {
		t.Fatal("expected non-nil Uploader")
	}
	if u.Running() {
		t.Error("uploader should not be running before Run()")
	}
}

func TestUploadSingleFixture(t *testing.T) {
	dir := t.TempDir()
	client := &mockS3Client{}
	fi := writeFixtureFile(t, dir, "fix-001", map[string]any{"hello": "world"})
	lister := &mockSpoolLister{fixtures: []spool.FixtureInfo{fi}}

	cfg := testConfig()
	cfg.ScanInterval = 20 * time.Millisecond
	u, err := New(client, lister, cfg, testLogger())
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())

	done := make(chan struct{})
	go func() {
		defer close(done)
		u.Run(ctx)
	}()

	// Wait for the upload to complete.
	deadline := time.After(5 * time.Second)
	for {
		if u.UploadsCompleted() >= 1 {
			break
		}
		select {
		case <-deadline:
			t.Fatal("timed out waiting for upload to complete")
		case <-time.After(10 * time.Millisecond):
		}
	}

	cancel()
	<-done

	// Verify S3 call.
	calls := client.getCalls()
	if len(calls) != 1 {
		t.Fatalf("expected 1 S3 call, got %d", len(calls))
	}
	if calls[0].Key != "fixtures/fix-001/fixture.json" {
		t.Errorf("key = %q, want %q", calls[0].Key, "fixtures/fix-001/fixture.json")
	}
	if calls[0].ContentType != "application/json" {
		t.Errorf("content_type = %q, want %q", calls[0].ContentType, "application/json")
	}

	// Verify local cleanup.
	if _, err := os.Stat(filepath.Join(dir, "fix-001")); !os.IsNotExist(err) {
		t.Error("fixture directory should have been removed after upload")
	}

	// Verify counters.
	if u.UploadsCompleted() != 1 {
		t.Errorf("UploadsCompleted = %d, want 1", u.UploadsCompleted())
	}
	if u.UploadsFailed() != 0 {
		t.Errorf("UploadsFailed = %d, want 0", u.UploadsFailed())
	}
	if u.BytesUploaded() <= 0 {
		t.Error("BytesUploaded should be > 0")
	}
	if !u.Running() == true {
		// After cancel + <-done, Running should be false.
	}
}

func TestUploadRetryThenSucceed(t *testing.T) {
	dir := t.TempDir()
	client := &mockS3Client{
		err:   errors.New("transient"),
		failN: 2, // fail first 2 attempts, succeed on 3rd
	}
	fi := writeFixtureFile(t, dir, "fix-retry", map[string]any{"retry": true})
	lister := &mockSpoolLister{fixtures: []spool.FixtureInfo{fi}}

	cfg := testConfig()
	cfg.ScanInterval = 20 * time.Millisecond
	cfg.MaxRetries = 3
	u, err := New(client, lister, cfg, testLogger())
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		u.Run(ctx)
	}()

	deadline := time.After(10 * time.Second)
	for {
		if u.UploadsCompleted() >= 1 {
			break
		}
		select {
		case <-deadline:
			t.Fatal("timed out waiting for upload to succeed after retries")
		case <-time.After(10 * time.Millisecond):
		}
	}

	cancel()
	<-done

	calls := client.getCalls()
	if len(calls) != 3 {
		t.Fatalf("expected 3 S3 calls (2 failed + 1 success), got %d", len(calls))
	}
	if u.UploadsCompleted() != 1 {
		t.Errorf("UploadsCompleted = %d, want 1", u.UploadsCompleted())
	}
	if u.UploadsFailed() != 0 {
		t.Errorf("UploadsFailed = %d, want 0 (retries succeeded)", u.UploadsFailed())
	}
}

func TestUploadAllRetriesFail(t *testing.T) {
	dir := t.TempDir()
	client := &mockS3Client{err: errors.New("permanent failure")}
	fi := writeFixtureFile(t, dir, "fix-fail", map[string]any{"fail": true})
	lister := &mockSpoolLister{fixtures: []spool.FixtureInfo{fi}}

	cfg := testConfig()
	cfg.ScanInterval = 20 * time.Millisecond
	cfg.MaxRetries = 2
	u, err := New(client, lister, cfg, testLogger())
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		u.Run(ctx)
	}()

	deadline := time.After(10 * time.Second)
	for {
		if u.UploadsFailed() >= 1 {
			break
		}
		select {
		case <-deadline:
			t.Fatal("timed out waiting for upload failure")
		case <-time.After(10 * time.Millisecond):
		}
	}

	cancel()
	<-done

	// Fixture directory should NOT be deleted on failure.
	if _, err := os.Stat(filepath.Join(dir, "fix-fail")); os.IsNotExist(err) {
		t.Error("fixture directory should be preserved on upload failure")
	}
	if u.UploadsCompleted() != 0 {
		t.Errorf("UploadsCompleted = %d, want 0", u.UploadsCompleted())
	}
	if u.UploadsFailed() != 1 {
		t.Errorf("UploadsFailed = %d, want 1", u.UploadsFailed())
	}
	if u.LastError() == "" {
		t.Error("LastError should be set after failed upload")
	}
}

func TestScanSkipsInFlightFixtures(t *testing.T) {
	dir := t.TempDir()
	client := &mockS3Client{}
	fi := writeFixtureFile(t, dir, "fix-inflight", map[string]any{"data": 1})
	lister := &mockSpoolLister{fixtures: []spool.FixtureInfo{fi}}

	cfg := testConfig()
	cfg.Workers = 1
	cfg.ScanInterval = 20 * time.Millisecond
	u, err := New(client, lister, cfg, testLogger())
	if err != nil {
		t.Fatal(err)
	}

	// Manually mark fixture as in-flight.
	u.markInFlight("fix-inflight")

	ctx := context.Background()
	u.scan(ctx)

	// Jobs channel should be empty — the fixture was skipped.
	select {
	case <-u.jobs:
		t.Error("expected no jobs dispatched for in-flight fixture")
	default:
		// OK
	}

	u.clearInFlight("fix-inflight")
}

func TestScanHandlesListError(t *testing.T) {
	lister := &mockSpoolLister{err: errors.New("disk error")}
	cfg := testConfig()
	u, err := New(&mockS3Client{}, lister, cfg, testLogger())
	if err != nil {
		t.Fatal(err)
	}

	u.scan(context.Background())

	if u.LastError() == "" {
		t.Error("LastError should be set after list error")
	}
}

func TestVanishedFixture(t *testing.T) {
	dir := t.TempDir()
	client := &mockS3Client{}

	// Create a FixtureInfo pointing to a path that doesn't exist.
	fi := spool.FixtureInfo{
		Path:      filepath.Join(dir, "vanished"),
		FixtureID: "vanished",
		ModTime:   time.Now(),
		SizeBytes: 100,
	}
	lister := &mockSpoolLister{fixtures: []spool.FixtureInfo{fi}}

	cfg := testConfig()
	cfg.ScanInterval = 20 * time.Millisecond
	u, err := New(client, lister, cfg, testLogger())
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		u.Run(ctx)
	}()

	// Give the uploader a moment to process.
	time.Sleep(200 * time.Millisecond)
	cancel()
	<-done

	// Should not have uploaded anything or counted as failure.
	calls := client.getCalls()
	if len(calls) != 0 {
		t.Errorf("expected 0 S3 calls for vanished fixture, got %d", len(calls))
	}
	if u.UploadsFailed() != 0 {
		t.Errorf("UploadsFailed = %d, want 0 for vanished fixture", u.UploadsFailed())
	}
}

func TestMultipleFixtures(t *testing.T) {
	dir := t.TempDir()
	client := &mockS3Client{}

	var fixtures []spool.FixtureInfo
	for i := 0; i < 5; i++ {
		id := "fix-multi-" + string(rune('a'+i))
		fi := writeFixtureFile(t, dir, id, map[string]any{"index": i})
		fixtures = append(fixtures, fi)
	}
	lister := &mockSpoolLister{fixtures: fixtures}

	cfg := testConfig()
	cfg.Workers = 3
	cfg.ScanInterval = 20 * time.Millisecond
	u, err := New(client, lister, cfg, testLogger())
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		u.Run(ctx)
	}()

	deadline := time.After(10 * time.Second)
	for {
		if u.UploadsCompleted() >= 5 {
			break
		}
		select {
		case <-deadline:
			t.Fatalf("timed out: completed=%d, failed=%d", u.UploadsCompleted(), u.UploadsFailed())
		case <-time.After(10 * time.Millisecond):
		}
	}

	cancel()
	<-done

	calls := client.getCalls()
	if len(calls) != 5 {
		t.Fatalf("expected 5 S3 calls, got %d", len(calls))
	}
}

func TestRunningState(t *testing.T) {
	cfg := testConfig()
	cfg.ScanInterval = 50 * time.Millisecond
	u, err := New(&mockS3Client{}, &mockSpoolLister{}, cfg, testLogger())
	if err != nil {
		t.Fatal(err)
	}

	if u.Running() {
		t.Error("should not be running before Run()")
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		u.Run(ctx)
	}()

	// Wait for Running to become true.
	deadline := time.After(2 * time.Second)
	for !u.Running() {
		select {
		case <-deadline:
			t.Fatal("timed out waiting for Running=true")
		case <-time.After(5 * time.Millisecond):
		}
	}

	cancel()
	<-done

	if u.Running() {
		t.Error("should not be running after Run() returns")
	}
}

// ─── S3 Key Tests ───────────────────────────────────────────────────────────

func TestS3Key(t *testing.T) {
	tests := []struct {
		prefix    string
		fixtureID string
		want      string
	}{
		{"", "abc-123", "abc-123/fixture.json"},
		{"my-prefix", "abc-123", "my-prefix/abc-123/fixture.json"},
		{"a/b/c", "fix-1", "a/b/c/fix-1/fixture.json"},
	}
	for _, tt := range tests {
		got := s3Key(tt.prefix, tt.fixtureID)
		if got != tt.want {
			t.Errorf("s3Key(%q, %q) = %q, want %q", tt.prefix, tt.fixtureID, got, tt.want)
		}
	}
}

// ─── Backoff Tests ──────────────────────────────────────────────────────────

func TestBackoffDuration(t *testing.T) {
	tests := []struct {
		attempt int
		want    time.Duration
	}{
		{0, 500 * time.Millisecond},
		{1, 1 * time.Second},
		{2, 2 * time.Second},
		{3, 4 * time.Second},
		{10, 30 * time.Second}, // capped
		{20, 30 * time.Second}, // capped
	}
	for _, tt := range tests {
		got := backoffDuration(tt.attempt)
		if got != tt.want {
			t.Errorf("backoffDuration(%d) = %v, want %v", tt.attempt, got, tt.want)
		}
	}
}

// ─── Config Validation Tests ────────────────────────────────────────────────

func TestConfigValidation(t *testing.T) {
	valid := testConfig()
	if err := valid.Validate(); err != nil {
		t.Errorf("valid config should not error: %v", err)
	}

	tests := []struct {
		name   string
		modify func(*UploaderConfig)
	}{
		{"empty bucket", func(c *UploaderConfig) { c.Bucket = "" }},
		{"empty region", func(c *UploaderConfig) { c.Region = "" }},
		{"zero workers", func(c *UploaderConfig) { c.Workers = 0 }},
		{"zero interval", func(c *UploaderConfig) { c.ScanInterval = 0 }},
		{"zero retries", func(c *UploaderConfig) { c.MaxRetries = 0 }},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cfg := testConfig()
			tt.modify(&cfg)
			if err := cfg.Validate(); err == nil {
				t.Error("expected validation error")
			}
		})
	}
}

func TestUploadUsesStructuredKey(t *testing.T) {
	dir := t.TempDir()
	client := &mockS3Client{}

	// Create a fixture with method/path/service metadata in the payload.
	payload := map[string]any{
		"schema_version": 1,
		"fixture_id":     "fix-structured",
		"session_id":     "sess-1",
		"created_at_ms":  time.Date(2026, 3, 21, 14, 30, 0, 0, time.UTC).UnixMilli(),
		"service":        "pricing-api",
		"golden_output": map[string]any{
			"method": "POST",
			"path":   "/quote",
		},
	}
	fi := writeFixtureFile(t, dir, "fix-structured", payload)
	lister := &mockSpoolLister{fixtures: []spool.FixtureInfo{fi}}

	cfg := testConfig()
	cfg.ScanInterval = 20 * time.Millisecond
	u, err := New(client, lister, cfg, testLogger())
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		u.Run(ctx)
	}()

	deadline := time.After(5 * time.Second)
	for {
		if u.UploadsCompleted() >= 1 {
			break
		}
		select {
		case <-deadline:
			t.Fatal("timed out waiting for upload")
		case <-time.After(10 * time.Millisecond):
		}
	}

	cancel()
	<-done

	calls := client.getCalls()
	if len(calls) != 1 {
		t.Fatalf("expected 1 S3 call, got %d", len(calls))
	}

	// The key should be structured: fixtures/pricing-api/post_quote/2026-03-21/fix-structured.json
	wantKey := "fixtures/pricing-api/post_quote/2026-03-21/fix-structured.json"
	if calls[0].Key != wantKey {
		t.Errorf("S3 key = %q, want %q", calls[0].Key, wantKey)
	}
}
