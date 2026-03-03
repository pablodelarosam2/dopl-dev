package spool

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// --- helpers ---

// goodBundle returns a FixtureBundle that passes ValidateBasic.
func goodBundle(fixtureID string) FixtureBundle {
	return FixtureBundle{
		SchemaVersion: 1,
		FixtureID:     fixtureID,
		SessionID:     "sess-001",
		CreatedAtMs:   time.Now().UnixMilli(),
		Service:       json.RawMessage(`{"name":"svc"}`),
		Input:         json.RawMessage(`{"key":"value"}`),
		Stubs:         json.RawMessage(`[]`),
		GoldenOutput:  json.RawMessage(`{"out":true}`),
		Metadata:      json.RawMessage(`{"status":"SUCCESS"}`),
	}
}

// newTestWriter creates a Writer backed by t.TempDir with a generous quota.
func newTestWriter(t *testing.T, maxBytes int64) *Writer {
	t.Helper()
	w, err := NewWriter(SpoolConfig{
		SpoolDir:      t.TempDir(),
		MaxSpoolBytes: maxBytes,
	})
	if err != nil {
		t.Fatalf("NewWriter: %v", err)
	}
	if err := w.InitScan(); err != nil {
		t.Fatalf("InitScan: %v", err)
	}
	return w
}

// =====================================================================
// types.go — ValidateBasic
// =====================================================================

func TestValidateBasic_ValidBundle(t *testing.T) {
	b := goodBundle("fix-001")
	if err := b.ValidateBasic(); err != nil {
		t.Errorf("ValidateBasic() = %v, want nil", err)
	}
}

func TestValidateBasic_MissingFixtureID(t *testing.T) {
	b := goodBundle("fix-001")
	b.FixtureID = ""
	if err := b.ValidateBasic(); err == nil {
		t.Error("ValidateBasic() = nil, want error for empty fixture_id")
	}
}

func TestValidateBasic_MissingSessionID(t *testing.T) {
	b := goodBundle("fix-001")
	b.SessionID = ""
	if err := b.ValidateBasic(); err == nil {
		t.Error("ValidateBasic() = nil, want error for empty session_id")
	}
}

func TestValidateBasic_ZeroSchemaVersion(t *testing.T) {
	b := goodBundle("fix-001")
	b.SchemaVersion = 0
	if err := b.ValidateBasic(); err == nil {
		t.Error("ValidateBasic() = nil, want error for schema_version 0")
	}
}

func TestValidateBasic_ZeroCreatedAt(t *testing.T) {
	b := goodBundle("fix-001")
	b.CreatedAtMs = 0
	if err := b.ValidateBasic(); err == nil {
		t.Error("ValidateBasic() = nil, want error for created_at_ms 0")
	}
}

func TestValidateBasic_FixtureIDWithSlash(t *testing.T) {
	b := goodBundle("bad/id")
	if err := b.ValidateBasic(); err == nil {
		t.Error("ValidateBasic() = nil, want error for fixture_id with slash")
	}
}

func TestValidateBasic_FixtureIDWithDot(t *testing.T) {
	b := goodBundle("bad.id")
	if err := b.ValidateBasic(); err == nil {
		t.Error("ValidateBasic() = nil, want error for fixture_id with dot")
	}
}

func TestValidateBasic_FixtureIDTooLong(t *testing.T) {
	b := goodBundle(strings.Repeat("a", maxIDLen+1))
	if err := b.ValidateBasic(); err == nil {
		t.Error("ValidateBasic() = nil, want error for overly long fixture_id")
	}
}

func TestValidateBasic_FixtureIDAtMaxLen(t *testing.T) {
	b := goodBundle(strings.Repeat("a", maxIDLen))
	if err := b.ValidateBasic(); err != nil {
		t.Errorf("ValidateBasic() = %v, want nil for fixture_id at max length", err)
	}
}

// =====================================================================
// layout.go — path helpers
// =====================================================================

func TestFixtureDir(t *testing.T) {
	got := FixtureDir("/var/spool", "fx-001")
	want := filepath.Join("/var/spool", "fx-001")
	if got != want {
		t.Errorf("FixtureDir() = %q, want %q", got, want)
	}
}

func TestTempFixtureDir(t *testing.T) {
	got := TempFixtureDir("/var/spool", "fx-001")
	want := filepath.Join("/var/spool", "fx-001.tmp")
	if got != want {
		t.Errorf("TempFixtureDir() = %q, want %q", got, want)
	}
}

func TestFixtureFilePath(t *testing.T) {
	got := FixtureFilePath("/var/spool/fx-001")
	want := filepath.Join("/var/spool/fx-001", "fixture.json")
	if got != want {
		t.Errorf("FixtureFilePath() = %q, want %q", got, want)
	}
}

func TestIsTempDirName_TrueForTmp(t *testing.T) {
	if !IsTempDirName("fx-001.tmp") {
		t.Error("IsTempDirName(\"fx-001.tmp\") = false, want true")
	}
}

func TestIsTempDirName_FalseForCommitted(t *testing.T) {
	if IsTempDirName("fx-001") {
		t.Error("IsTempDirName(\"fx-001\") = true, want false")
	}
}

func TestSanitizeFixtureID_ValidID(t *testing.T) {
	if err := SanitizeFixtureID("fx-001_test"); err != nil {
		t.Errorf("SanitizeFixtureID() = %v, want nil", err)
	}
}

func TestSanitizeFixtureID_PathTraversal(t *testing.T) {
	ids := []string{"../etc", "foo/bar", "foo\\bar", ".hidden"}
	for _, id := range ids {
		if err := SanitizeFixtureID(id); err == nil {
			t.Errorf("SanitizeFixtureID(%q) = nil, want error", id)
		}
	}
}

func TestSanitizeFixtureID_Empty(t *testing.T) {
	if err := SanitizeFixtureID(""); err == nil {
		t.Error("SanitizeFixtureID(\"\") = nil, want error")
	}
}

// =====================================================================
// SpoolConfig validation
// =====================================================================

func TestSpoolConfig_Validate_Valid(t *testing.T) {
	cfg := SpoolConfig{SpoolDir: "/tmp/spool", MaxSpoolBytes: 1024}
	if err := cfg.Validate(); err != nil {
		t.Errorf("Validate() = %v, want nil", err)
	}
}

func TestSpoolConfig_Validate_EmptyDir(t *testing.T) {
	cfg := SpoolConfig{SpoolDir: "", MaxSpoolBytes: 1024}
	if err := cfg.Validate(); err == nil {
		t.Error("Validate() = nil, want error for empty SpoolDir")
	}
}

func TestSpoolConfig_Validate_ZeroMaxBytes(t *testing.T) {
	cfg := SpoolConfig{SpoolDir: "/tmp/spool", MaxSpoolBytes: 0}
	if err := cfg.Validate(); err == nil {
		t.Error("Validate() = nil, want error for MaxSpoolBytes = 0")
	}
}

// =====================================================================
// writer.go — NewWriter
// =====================================================================

func TestNewWriter_CreatesSpoolDir(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "sub", "spool")
	_, err := NewWriter(SpoolConfig{SpoolDir: dir, MaxSpoolBytes: 1024})
	if err != nil {
		t.Fatalf("NewWriter() error = %v", err)
	}

	info, err := os.Stat(dir)
	if err != nil {
		t.Fatalf("spool dir not created: %v", err)
	}
	if !info.IsDir() {
		t.Error("spool path is not a directory")
	}
}

func TestNewWriter_InvalidConfigReturnsError(t *testing.T) {
	_, err := NewWriter(SpoolConfig{SpoolDir: "", MaxSpoolBytes: 1024})
	if err == nil {
		t.Error("NewWriter() = nil error, want error for empty SpoolDir")
	}
}

func TestNewWriter_WritableByDefault(t *testing.T) {
	w, err := NewWriter(SpoolConfig{SpoolDir: t.TempDir(), MaxSpoolBytes: 1024})
	if err != nil {
		t.Fatalf("NewWriter: %v", err)
	}
	if !w.Writable() {
		t.Error("Writable() = false after NewWriter, want true")
	}
}

// =====================================================================
// writer.go — WriteFixture happy path
// =====================================================================

func TestWriteFixture_CreatesFixtureJSON(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	bundle := goodBundle("fx-happy")

	result, err := w.WriteFixture(bundle)
	if err != nil {
		t.Fatalf("WriteFixture() error = %v", err)
	}
	if result.Dropped {
		t.Fatalf("WriteFixture() Dropped = true, want false")
	}
	if result.BytesWritten <= 0 {
		t.Errorf("BytesWritten = %d, want > 0", result.BytesWritten)
	}

	fPath := FixtureFilePath(FixtureDir(w.cfg.SpoolDir, "fx-happy"))
	data, err := os.ReadFile(fPath)
	if err != nil {
		t.Fatalf("fixture.json not found: %v", err)
	}

	var decoded FixtureBundle
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("fixture.json invalid JSON: %v", err)
	}
	if decoded.FixtureID != "fx-happy" {
		t.Errorf("decoded fixture_id = %q, want fx-happy", decoded.FixtureID)
	}
	if decoded.SchemaVersion != 1 {
		t.Errorf("decoded schema_version = %d, want 1", decoded.SchemaVersion)
	}
}

func TestWriteFixture_NoTmpDirsRemainAfterSuccess(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	if _, err := w.WriteFixture(goodBundle("fx-atomic")); err != nil {
		t.Fatalf("WriteFixture() error = %v", err)
	}

	entries, err := os.ReadDir(w.cfg.SpoolDir)
	if err != nil {
		t.Fatalf("ReadDir: %v", err)
	}
	for _, e := range entries {
		if IsTempDirName(e.Name()) {
			t.Errorf("stale temp dir found after successful write: %s", e.Name())
		}
	}
}

func TestWriteFixture_MultipleFixtures(t *testing.T) {
	w := newTestWriter(t, 1024*1024)

	for _, id := range []string{"fx-01", "fx-02", "fx-03"} {
		if _, err := w.WriteFixture(goodBundle(id)); err != nil {
			t.Fatalf("WriteFixture(%s) error = %v", id, err)
		}
	}

	entries, err := os.ReadDir(w.cfg.SpoolDir)
	if err != nil {
		t.Fatalf("ReadDir: %v", err)
	}

	dirs := 0
	for _, e := range entries {
		if e.IsDir() && !IsTempDirName(e.Name()) {
			dirs++
		}
	}
	if dirs != 3 {
		t.Errorf("committed dirs = %d, want 3", dirs)
	}
}

// =====================================================================
// writer.go — WriteFixture validation failures
// =====================================================================

func TestWriteFixture_RejectsInvalidBundle(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	bad := goodBundle("fx-bad")
	bad.SchemaVersion = 0

	_, err := w.WriteFixture(bad)
	if err == nil {
		t.Error("WriteFixture() = nil error, want error for invalid bundle")
	}
}

func TestWriteFixture_RejectsUnsafeFixtureID(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	bad := goodBundle("../escape")

	_, err := w.WriteFixture(bad)
	if err == nil {
		t.Error("WriteFixture() = nil error, want error for path-traversal fixture_id")
	}
}

// =====================================================================
// state.go — SpoolBytes tracking
// =====================================================================

func TestSpoolBytes_ZeroInitially(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	if w.SpoolBytes() != 0 {
		t.Errorf("SpoolBytes() = %d, want 0 on empty spool", w.SpoolBytes())
	}
}

func TestSpoolBytes_IncreasesAfterWrite(t *testing.T) {
	w := newTestWriter(t, 1024*1024)

	result, err := w.WriteFixture(goodBundle("fx-size"))
	if err != nil {
		t.Fatalf("WriteFixture: %v", err)
	}

	if w.SpoolBytes() != result.BytesWritten {
		t.Errorf("SpoolBytes() = %d, want %d", w.SpoolBytes(), result.BytesWritten)
	}
}

func TestSpoolBytes_AccumulatesAcrossWrites(t *testing.T) {
	w := newTestWriter(t, 1024*1024)

	r1, _ := w.WriteFixture(goodBundle("fx-a"))
	r2, _ := w.WriteFixture(goodBundle("fx-b"))

	want := r1.BytesWritten + r2.BytesWritten
	if w.SpoolBytes() != want {
		t.Errorf("SpoolBytes() = %d, want %d", w.SpoolBytes(), want)
	}
}

func TestMaxSpoolBytes_ReflectsConfig(t *testing.T) {
	w := newTestWriter(t, 42000)
	if w.MaxSpoolBytes() != 42000 {
		t.Errorf("MaxSpoolBytes() = %d, want 42000", w.MaxSpoolBytes())
	}
}

// =====================================================================
// Quota enforcement — drops and eviction
// =====================================================================

func TestWriteFixture_DropsWhenSpoolFull(t *testing.T) {
	// Tiny quota: just enough for ~1 fixture but not 2.
	w := newTestWriter(t, 200)

	r1, err := w.WriteFixture(goodBundle("fx-first"))
	if err != nil {
		t.Fatalf("first WriteFixture: %v", err)
	}
	if r1.Dropped {
		t.Fatal("first fixture should not be dropped")
	}

	// Eviction should kick in and delete fx-first to make room for fx-second.
	r2, err := w.WriteFixture(goodBundle("fx-second"))
	if err != nil {
		t.Fatalf("second WriteFixture: %v", err)
	}

	// With the quota this tight, either it evicted the first and wrote the
	// second, or it dropped the second. Either is valid — verify consistency.
	if r2.Dropped {
		// Fixture was too large even after eviction.
		if r2.DropReason != "spool_full" {
			t.Errorf("DropReason = %q, want spool_full", r2.DropReason)
		}
	} else {
		// First must have been evicted.
		_, err := os.Stat(FixtureDir(w.cfg.SpoolDir, "fx-first"))
		if err == nil {
			t.Error("fx-first should have been evicted but still exists")
		}
	}
}

func TestWriteFixture_DropsWhenSingleFixtureExceedsQuota(t *testing.T) {
	// Quota smaller than a single fixture (~170+ bytes marshalled).
	w := newTestWriter(t, 10)

	result, err := w.WriteFixture(goodBundle("fx-huge"))
	if err != nil {
		t.Fatalf("WriteFixture: %v", err)
	}
	if !result.Dropped {
		t.Error("Dropped = false, want true when fixture exceeds total quota")
	}
	if result.DropReason != "spool_full" {
		t.Errorf("DropReason = %q, want spool_full", result.DropReason)
	}
}

func TestEnsureCapacity_DeletesOldestFirst(t *testing.T) {
	w := newTestWriter(t, 1024*1024)

	// Write three fixtures with a small pause so mtimes differ.
	ids := []string{"fx-oldest", "fx-middle", "fx-newest"}
	for _, id := range ids {
		if _, err := w.WriteFixture(goodBundle(id)); err != nil {
			t.Fatalf("WriteFixture(%s): %v", id, err)
		}
		// Touch the directories to guarantee ordering — we back-date the oldest.
	}

	// Back-date fx-oldest and fx-middle so eviction order is deterministic.
	oldest := FixtureDir(w.cfg.SpoolDir, "fx-oldest")
	middle := FixtureDir(w.cfg.SpoolDir, "fx-middle")
	past := time.Now().Add(-10 * time.Minute)
	farPast := time.Now().Add(-20 * time.Minute)
	os.Chtimes(oldest, farPast, farPast)
	os.Chtimes(middle, past, past)

	// Shrink quota so only one fixture fits.
	w.cfg.MaxSpoolBytes = w.SpoolBytes()/3 + 1

	// Write a new fixture — should evict fx-oldest first, then fx-middle if needed.
	result, err := w.WriteFixture(goodBundle("fx-new"))
	if err != nil {
		t.Fatalf("WriteFixture(fx-new): %v", err)
	}
	if result.Dropped {
		t.Fatal("fx-new was dropped, expected eviction to make room")
	}

	// fx-oldest must be gone.
	if _, err := os.Stat(oldest); err == nil {
		t.Error("fx-oldest still exists after eviction, want deleted")
	}
}

// =====================================================================
// Crash recovery — InitScan
// =====================================================================

func TestInitScan_RemovesStaleTmpDirs(t *testing.T) {
	dir := t.TempDir()
	w, err := NewWriter(SpoolConfig{SpoolDir: dir, MaxSpoolBytes: 1024 * 1024})
	if err != nil {
		t.Fatalf("NewWriter: %v", err)
	}

	// Simulate a crash: leave a .tmp directory behind.
	staleDir := TempFixtureDir(dir, "fx-crashed")
	if err := os.MkdirAll(staleDir, 0o755); err != nil {
		t.Fatalf("mkdir stale tmp: %v", err)
	}
	if err := os.WriteFile(FixtureFilePath(staleDir), []byte(`{}`), 0o644); err != nil {
		t.Fatalf("write stale fixture: %v", err)
	}

	if err := w.InitScan(); err != nil {
		t.Fatalf("InitScan: %v", err)
	}

	if _, err := os.Stat(staleDir); err == nil {
		t.Error("stale .tmp dir still exists after InitScan")
	}
}

func TestInitScan_ComputesCorrectSize(t *testing.T) {
	dir := t.TempDir()
	w, err := NewWriter(SpoolConfig{SpoolDir: dir, MaxSpoolBytes: 1024 * 1024})
	if err != nil {
		t.Fatalf("NewWriter: %v", err)
	}

	// Pre-populate two committed fixture dirs manually.
	payload1 := []byte(`{"schema_version":1,"fixture_id":"fx-1","session_id":"s","created_at_ms":1}`)
	payload2 := []byte(`{"schema_version":1,"fixture_id":"fx-2","session_id":"s","created_at_ms":1}`)

	for _, p := range []struct {
		id   string
		data []byte
	}{{"fx-1", payload1}, {"fx-2", payload2}} {
		d := FixtureDir(dir, p.id)
		os.MkdirAll(d, 0o755)
		os.WriteFile(FixtureFilePath(d), p.data, 0o644)
	}

	if err := w.InitScan(); err != nil {
		t.Fatalf("InitScan: %v", err)
	}

	want := int64(len(payload1) + len(payload2))
	if w.SpoolBytes() != want {
		t.Errorf("SpoolBytes() = %d, want %d", w.SpoolBytes(), want)
	}
}

func TestInitScan_IgnoresNonDirEntries(t *testing.T) {
	dir := t.TempDir()
	w, err := NewWriter(SpoolConfig{SpoolDir: dir, MaxSpoolBytes: 1024 * 1024})
	if err != nil {
		t.Fatalf("NewWriter: %v", err)
	}

	// Drop a stray file in the spool dir — should be silently ignored.
	os.WriteFile(filepath.Join(dir, "stray.txt"), []byte("oops"), 0o644)

	if err := w.InitScan(); err != nil {
		t.Fatalf("InitScan: %v", err)
	}
	if w.SpoolBytes() != 0 {
		t.Errorf("SpoolBytes() = %d, want 0", w.SpoolBytes())
	}
}

// =====================================================================
// state.go — Writable / LastError
// =====================================================================

func TestWritable_TrueAfterSuccessfulWrite(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	w.WriteFixture(goodBundle("fx-ok"))

	if !w.Writable() {
		t.Error("Writable() = false after successful write, want true")
	}
	if w.LastError() != "" {
		t.Errorf("LastError() = %q, want empty", w.LastError())
	}
}

func TestSetError_SetsWritableFalse(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	w.setError(os.ErrPermission)

	if w.Writable() {
		t.Error("Writable() = true after setError, want false")
	}
	if w.LastError() == "" {
		t.Error("LastError() is empty after setError, want non-empty")
	}
}

func TestClearError_ResetsState(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	w.setError(os.ErrPermission)
	w.clearError()

	if !w.Writable() {
		t.Error("Writable() = false after clearError, want true")
	}
	if w.LastError() != "" {
		t.Errorf("LastError() = %q after clearError, want empty", w.LastError())
	}
}

// =====================================================================
// Edge cases
// =====================================================================

func TestWriteFixture_MalformedBundleDoesNotCreateDir(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	bad := FixtureBundle{} // all zero-values

	_, err := w.WriteFixture(bad)
	if err == nil {
		t.Fatal("WriteFixture() = nil error for zero-value bundle, want error")
	}

	entries, _ := os.ReadDir(w.cfg.SpoolDir)
	for _, e := range entries {
		if e.IsDir() {
			t.Errorf("unexpected directory %q created for malformed bundle", e.Name())
		}
	}
}

func TestWriteResult_DroppedFalseByDefault(t *testing.T) {
	var r WriteResult
	if r.Dropped {
		t.Error("zero-value WriteResult.Dropped = true, want false")
	}
}

func TestFixtureInfo_ZeroValue(t *testing.T) {
	var fi FixtureInfo
	if fi.Path != "" || fi.FixtureID != "" || fi.SizeBytes != 0 {
		t.Error("zero-value FixtureInfo should have all empty/zero fields")
	}
}

func TestWriteFixture_FixtureJSONContainsAllFields(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	bundle := goodBundle("fx-fields")
	w.WriteFixture(bundle)

	data, err := os.ReadFile(FixtureFilePath(FixtureDir(w.cfg.SpoolDir, "fx-fields")))
	if err != nil {
		t.Fatalf("read fixture.json: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	required := []string{"schema_version", "fixture_id", "session_id", "created_at_ms"}
	for _, key := range required {
		if _, ok := raw[key]; !ok {
			t.Errorf("fixture.json missing required key %q", key)
		}
	}
}

func TestRecoverTempDirs_NoopWhenClean(t *testing.T) {
	w := newTestWriter(t, 1024*1024)
	// Write a real fixture, then call RecoverTempDirs — should not touch it.
	w.WriteFixture(goodBundle("fx-keep"))

	if err := w.RecoverTempDirs(); err != nil {
		t.Fatalf("RecoverTempDirs: %v", err)
	}

	if _, err := os.Stat(FixtureDir(w.cfg.SpoolDir, "fx-keep")); err != nil {
		t.Error("RecoverTempDirs removed a committed fixture")
	}
}

func TestRecoverTempDirs_RemovesMultipleTmpDirs(t *testing.T) {
	dir := t.TempDir()
	w, _ := NewWriter(SpoolConfig{SpoolDir: dir, MaxSpoolBytes: 1024 * 1024})

	for _, id := range []string{"a", "b", "c"} {
		os.MkdirAll(TempFixtureDir(dir, id), 0o755)
	}

	if err := w.RecoverTempDirs(); err != nil {
		t.Fatalf("RecoverTempDirs: %v", err)
	}

	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if IsTempDirName(e.Name()) {
			t.Errorf("stale temp dir %q still exists", e.Name())
		}
	}
}

// =====================================================================
// spool.go — Spool facade
// =====================================================================

// newTestSpool creates a Spool backed by t.TempDir with the given quota.
func newTestSpool(t *testing.T, maxBytes int64) *Spool {
	t.Helper()
	sp, err := New(SpoolConfig{
		SpoolDir:      t.TempDir(),
		MaxSpoolBytes: maxBytes,
	})
	if err != nil {
		t.Fatalf("spool.New: %v", err)
	}
	return sp
}

func TestNew_CreatesSpoolDirectoryAndReturnsUsableSpool(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "nested", "spool")
	sp, err := New(SpoolConfig{SpoolDir: dir, MaxSpoolBytes: 1024 * 1024})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}
	if sp == nil {
		t.Fatal("New() returned nil spool")
	}

	if _, err := os.Stat(dir); err != nil {
		t.Fatalf("spool dir not created: %v", err)
	}
}

func TestNew_InvalidConfigReturnsError(t *testing.T) {
	_, err := New(SpoolConfig{SpoolDir: "", MaxSpoolBytes: 1024})
	if err == nil {
		t.Error("New() = nil error, want error for empty SpoolDir")
	}
}

func TestNew_RemovesStaleTmpDirsOnStartup(t *testing.T) {
	dir := t.TempDir()

	// Simulate a crash by pre-creating a .tmp directory.
	stale := TempFixtureDir(dir, "fx-crashed")
	os.MkdirAll(stale, 0o755)
	os.WriteFile(FixtureFilePath(stale), []byte(`{}`), 0o644)

	sp, err := New(SpoolConfig{SpoolDir: dir, MaxSpoolBytes: 1024 * 1024})
	if err != nil {
		t.Fatalf("New(): %v", err)
	}
	_ = sp

	if _, err := os.Stat(stale); err == nil {
		t.Error("stale .tmp dir still exists after New()")
	}
}

func TestNew_ComputesInitialBytesFromExistingFixtures(t *testing.T) {
	dir := t.TempDir()

	// Pre-populate a committed fixture.
	payload := []byte(`{"schema_version":1,"fixture_id":"fx-pre","session_id":"s","created_at_ms":1}`)
	fdir := FixtureDir(dir, "fx-pre")
	os.MkdirAll(fdir, 0o755)
	os.WriteFile(FixtureFilePath(fdir), payload, 0o644)

	sp, err := New(SpoolConfig{SpoolDir: dir, MaxSpoolBytes: 1024 * 1024})
	if err != nil {
		t.Fatalf("New(): %v", err)
	}

	if sp.Stats().SpoolBytes != int64(len(payload)) {
		t.Errorf("Stats().SpoolBytes = %d, want %d", sp.Stats().SpoolBytes, len(payload))
	}
}

// --- Commit ---

func TestCommit_WritesFixtureAndReturnsBytes(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)

	result, err := sp.Commit(goodBundle("fx-commit"))
	if err != nil {
		t.Fatalf("Commit() error = %v", err)
	}
	if result.Dropped {
		t.Fatal("Commit() Dropped = true, want false")
	}
	if result.BytesWritten <= 0 {
		t.Errorf("BytesWritten = %d, want > 0", result.BytesWritten)
	}

	fPath := FixtureFilePath(FixtureDir(sp.writer.cfg.SpoolDir, "fx-commit"))
	if _, err := os.Stat(fPath); err != nil {
		t.Errorf("fixture.json not found after Commit: %v", err)
	}
}

func TestCommit_DropsWhenQuotaExceeded(t *testing.T) {
	sp := newTestSpool(t, 10) // quota smaller than any marshalled bundle

	result, err := sp.Commit(goodBundle("fx-drop"))
	if err != nil {
		t.Fatalf("Commit() error = %v", err)
	}
	if !result.Dropped {
		t.Error("Commit() Dropped = false, want true when quota is tiny")
	}
	if result.DropReason != "spool_full" {
		t.Errorf("DropReason = %q, want spool_full", result.DropReason)
	}
}

func TestCommit_RejectsInvalidBundle(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)

	bad := goodBundle("fx-bad")
	bad.SchemaVersion = 0

	_, err := sp.Commit(bad)
	if err == nil {
		t.Error("Commit() = nil error, want error for invalid bundle")
	}
}

func TestCommit_NoTmpDirsRemainAfterSuccess(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)
	sp.Commit(goodBundle("fx-clean"))

	entries, _ := os.ReadDir(sp.writer.cfg.SpoolDir)
	for _, e := range entries {
		if IsTempDirName(e.Name()) {
			t.Errorf("stale .tmp dir %q after Commit", e.Name())
		}
	}
}

// --- Stats ---

func TestStats_InitiallyZeroBytesAndWritable(t *testing.T) {
	sp := newTestSpool(t, 512*1024)

	stats := sp.Stats()
	if stats.SpoolBytes != 0 {
		t.Errorf("SpoolBytes = %d, want 0", stats.SpoolBytes)
	}
	if !stats.Writable {
		t.Error("Writable = false, want true on fresh spool")
	}
	if stats.LastError != "" {
		t.Errorf("LastError = %q, want empty", stats.LastError)
	}
	if stats.MaxSpoolBytes != 512*1024 {
		t.Errorf("MaxSpoolBytes = %d, want %d", stats.MaxSpoolBytes, 512*1024)
	}
}

func TestStats_SpoolBytesIncreasesAfterCommit(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)

	result, _ := sp.Commit(goodBundle("fx-stat"))

	if sp.Stats().SpoolBytes != result.BytesWritten {
		t.Errorf("SpoolBytes = %d, want %d", sp.Stats().SpoolBytes, result.BytesWritten)
	}
}

// --- Recover ---

func TestRecover_RemovesStaleTmpDirs(t *testing.T) {
	dir := t.TempDir()
	sp, err := New(SpoolConfig{SpoolDir: dir, MaxSpoolBytes: 1024 * 1024})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	// Plant a stale .tmp dir after construction (simulates crash mid-write).
	stale := TempFixtureDir(dir, "fx-mid-crash")
	os.MkdirAll(stale, 0o755)

	if err := sp.Recover(); err != nil {
		t.Fatalf("Recover(): %v", err)
	}

	if _, err := os.Stat(stale); err == nil {
		t.Error("stale .tmp dir still exists after Recover()")
	}
}

func TestRecover_NoopWhenClean(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)
	sp.Commit(goodBundle("fx-legit"))

	if err := sp.Recover(); err != nil {
		t.Errorf("Recover() = %v on clean spool, want nil", err)
	}

	// The committed fixture must still be present.
	if _, err := os.Stat(FixtureDir(sp.writer.cfg.SpoolDir, "fx-legit")); err != nil {
		t.Error("committed fixture was removed by Recover()")
	}
}

// --- CleanupIfNeeded ---

func TestCleanupIfNeeded_FreesNothingWhenBelowQuota(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)
	sp.Commit(goodBundle("fx-below"))

	freed, err := sp.CleanupIfNeeded()
	if err != nil {
		t.Fatalf("CleanupIfNeeded() error = %v", err)
	}
	if freed != 0 {
		t.Errorf("freed = %d, want 0 when below quota", freed)
	}
}

func TestCleanupIfNeeded_EvictsWhenOverQuota(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)

	// Commit a fixture, then shrink the quota so the spool is now "over".
	sp.Commit(goodBundle("fx-over"))
	sp.writer.cfg.MaxSpoolBytes = 1 // force everything to be over quota

	freed, err := sp.CleanupIfNeeded()
	if err != nil {
		t.Fatalf("CleanupIfNeeded() error = %v", err)
	}
	if freed <= 0 {
		t.Errorf("freed = %d, want > 0 when over quota", freed)
	}
}

// --- List ---

func TestList_EmptyWhenNoFixtures(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)

	fixtures, err := sp.List()
	if err != nil {
		t.Fatalf("List() error = %v", err)
	}
	if len(fixtures) != 0 {
		t.Errorf("List() len = %d, want 0", len(fixtures))
	}
}

func TestList_ReturnsCommittedFixtures(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)

	sp.Commit(goodBundle("fx-list-1"))
	sp.Commit(goodBundle("fx-list-2"))

	fixtures, err := sp.List()
	if err != nil {
		t.Fatalf("List() error = %v", err)
	}
	if len(fixtures) != 2 {
		t.Errorf("List() len = %d, want 2", len(fixtures))
	}
}

func TestList_SkipsTmpDirs(t *testing.T) {
	sp := newTestSpool(t, 1024*1024)
	sp.Commit(goodBundle("fx-real"))

	// Plant a .tmp dir alongside the committed one.
	os.MkdirAll(TempFixtureDir(sp.writer.cfg.SpoolDir, "fx-inflight"), 0o755)

	fixtures, err := sp.List()
	if err != nil {
		t.Fatalf("List() error = %v", err)
	}
	if len(fixtures) != 1 {
		t.Errorf("List() len = %d, want 1 (.tmp must be excluded)", len(fixtures))
	}
}
