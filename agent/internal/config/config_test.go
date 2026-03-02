package config

import (
	"testing"
	"time"
)

// clearEnv unsets all AGENT_* environment variables so each test starts clean.
func clearEnv(t *testing.T) {
	t.Helper()
	keys := []string{
		"AGENT_LISTEN",
		"AGENT_SPOOL_DIR",
		"AGENT_MAX_SPOOL_BYTES",
		"AGENT_MAX_ACTIVE_SESSIONS",
		"AGENT_MAX_SESSION_BYTES",
		"AGENT_MAX_SESSION_AGE_MS",
		"AGENT_MAX_EVENT_BYTES",
		"AGENT_INGEST_QUEUE_SIZE",
		"AGENT_FLUSH_INTERVAL_MS",
		"AGENT_LOG_LEVEL",
	}
	for _, k := range keys {
		t.Setenv(k, "")
	}
}

// --- Load() ---

func TestLoad_Defaults(t *testing.T) {
	clearEnv(t)

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load() returned unexpected error: %v", err)
	}

	if cfg.ListenAddress != "127.0.0.1:7777" {
		t.Errorf("ListenAddress = %q, want 127.0.0.1:7777", cfg.ListenAddress)
	}
	if cfg.SpoolDir != "/tmp/record-agent" {
		t.Errorf("SpoolDir = %q, want /tmp/record-agent", cfg.SpoolDir)
	}
	if cfg.MaxSpoolBytes != 5*1024*1024*1024 {
		t.Errorf("MaxSpoolBytes = %d, want 5 GiB", cfg.MaxSpoolBytes)
	}
	if cfg.MaxActiveSessions != 1000 {
		t.Errorf("MaxActiveSessions = %d, want 1000", cfg.MaxActiveSessions)
	}
	if cfg.MaxSessionBytes != 1*1024*1024 {
		t.Errorf("MaxSessionBytes = %d, want 1 MiB", cfg.MaxSessionBytes)
	}
	if cfg.MaxSessionAge != 60*time.Second {
		t.Errorf("MaxSessionAge = %v, want 60s", cfg.MaxSessionAge)
	}
	if cfg.MaxEventBytes != 256*1024 {
		t.Errorf("MaxEventBytes = %d, want 256 KiB", cfg.MaxEventBytes)
	}
	if cfg.IngestQueueSize != 10000 {
		t.Errorf("IngestQueueSize = %d, want 10000", cfg.IngestQueueSize)
	}
	if cfg.FlushInterval != 2*time.Second {
		t.Errorf("FlushInterval = %v, want 2s", cfg.FlushInterval)
	}
	if cfg.LogLevel != "info" {
		t.Errorf("LogLevel = %q, want info", cfg.LogLevel)
	}
}

func TestLoad_EnvOverrides(t *testing.T) {
	clearEnv(t)
	t.Setenv("AGENT_LISTEN", "0.0.0.0:9090")
	t.Setenv("AGENT_SPOOL_DIR", "/data/spool")
	t.Setenv("AGENT_MAX_SPOOL_BYTES", "1073741824") // 1 GiB
	t.Setenv("AGENT_MAX_ACTIVE_SESSIONS", "50")
	t.Setenv("AGENT_MAX_SESSION_BYTES", "524288") // 512 KiB (< 1 GiB spool)
	t.Setenv("AGENT_MAX_SESSION_AGE_MS", "30000")
	t.Setenv("AGENT_MAX_EVENT_BYTES", "65536")
	t.Setenv("AGENT_INGEST_QUEUE_SIZE", "500")
	t.Setenv("AGENT_FLUSH_INTERVAL_MS", "5000")
	t.Setenv("AGENT_LOG_LEVEL", "debug")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load() returned unexpected error: %v", err)
	}

	if cfg.ListenAddress != "0.0.0.0:9090" {
		t.Errorf("ListenAddress = %q, want 0.0.0.0:9090", cfg.ListenAddress)
	}
	if cfg.SpoolDir != "/data/spool" {
		t.Errorf("SpoolDir = %q, want /data/spool", cfg.SpoolDir)
	}
	if cfg.MaxSpoolBytes != 1073741824 {
		t.Errorf("MaxSpoolBytes = %d, want 1073741824", cfg.MaxSpoolBytes)
	}
	if cfg.MaxActiveSessions != 50 {
		t.Errorf("MaxActiveSessions = %d, want 50", cfg.MaxActiveSessions)
	}
	if cfg.MaxSessionBytes != 524288 {
		t.Errorf("MaxSessionBytes = %d, want 524288", cfg.MaxSessionBytes)
	}
	if cfg.MaxSessionAge != 30*time.Second {
		t.Errorf("MaxSessionAge = %v, want 30s", cfg.MaxSessionAge)
	}
	if cfg.MaxEventBytes != 65536 {
		t.Errorf("MaxEventBytes = %d, want 65536", cfg.MaxEventBytes)
	}
	if cfg.IngestQueueSize != 500 {
		t.Errorf("IngestQueueSize = %d, want 500", cfg.IngestQueueSize)
	}
	if cfg.FlushInterval != 5*time.Second {
		t.Errorf("FlushInterval = %v, want 5s", cfg.FlushInterval)
	}
	if cfg.LogLevel != "debug" {
		t.Errorf("LogLevel = %q, want debug", cfg.LogLevel)
	}
}

// --- Validate() ---

func TestValidate_SpoolDirEmpty(t *testing.T) {
	cfg := validConfig()
	cfg.SpoolDir = ""

	if err := cfg.Validate(); err == nil {
		t.Error("expected error for empty SpoolDir, got nil")
	}
}

func TestValidate_MaxSpoolBytesZero(t *testing.T) {
	cfg := validConfig()
	cfg.MaxSpoolBytes = 0

	if err := cfg.Validate(); err == nil {
		t.Error("expected error for MaxSpoolBytes = 0, got nil")
	}
}

func TestValidate_MaxSpoolBytesNegative(t *testing.T) {
	cfg := validConfig()
	cfg.MaxSpoolBytes = -1

	if err := cfg.Validate(); err == nil {
		t.Error("expected error for MaxSpoolBytes < 0, got nil")
	}
}

func TestValidate_MaxSessionBytesZero(t *testing.T) {
	cfg := validConfig()
	cfg.MaxSessionBytes = 0

	if err := cfg.Validate(); err == nil {
		t.Error("expected error for MaxSessionBytes = 0, got nil")
	}
}

func TestValidate_MaxSessionBytesExceedsSpool(t *testing.T) {
	cfg := validConfig()
	cfg.MaxSessionBytes = cfg.MaxSpoolBytes + 1

	if err := cfg.Validate(); err == nil {
		t.Error("expected error when MaxSessionBytes > MaxSpoolBytes, got nil")
	}
}

func TestValidate_MaxActiveSessionsZero(t *testing.T) {
	cfg := validConfig()
	cfg.MaxActiveSessions = 0

	if err := cfg.Validate(); err == nil {
		t.Error("expected error for MaxActiveSessions = 0, got nil")
	}
}

func TestValidate_MaxActiveSessionsNegative(t *testing.T) {
	cfg := validConfig()
	cfg.MaxActiveSessions = -5

	if err := cfg.Validate(); err == nil {
		t.Error("expected error for MaxActiveSessions < 0, got nil")
	}
}

func TestValidate_ValidConfig(t *testing.T) {
	if err := validConfig().Validate(); err != nil {
		t.Errorf("Validate() returned unexpected error: %v", err)
	}
}

// --- Helper parsers with invalid env values ---

func TestGetEnv_ReturnsDefault_WhenUnset(t *testing.T) {
	t.Setenv("TEST_KEY_GETENV", "")
	got := getEnv("TEST_KEY_GETENV", "fallback")
	if got != "fallback" {
		t.Errorf("getEnv() = %q, want fallback", got)
	}
}

func TestGetEnv_ReturnsValue_WhenSet(t *testing.T) {
	t.Setenv("TEST_KEY_GETENV", "custom")
	got := getEnv("TEST_KEY_GETENV", "fallback")
	if got != "custom" {
		t.Errorf("getEnv() = %q, want custom", got)
	}
}

func TestParseInt64_InvalidValue_FallsBackToDefault(t *testing.T) {
	t.Setenv("TEST_INT64", "not-a-number")
	got := parseInt64("TEST_INT64", 42)
	if got != 42 {
		t.Errorf("parseInt64() = %d, want 42", got)
	}
}

func TestParseInt_InvalidValue_FallsBackToDefault(t *testing.T) {
	t.Setenv("TEST_INT", "bad")
	got := parseInt("TEST_INT", 7)
	if got != 7 {
		t.Errorf("parseInt() = %d, want 7", got)
	}
}

func TestParseDuration_InvalidValue_FallsBackToDefault(t *testing.T) {
	t.Setenv("TEST_DURATION", "not-a-duration")
	got := parseDuration("TEST_DURATION", 5*time.Second)
	if got != 5*time.Second {
		t.Errorf("parseDuration() = %v, want 5s", got)
	}
}

// validConfig returns a Config that passes Validate().
func validConfig() *Config {
	return &Config{
		SpoolDir:          "/tmp/spool",
		MaxSpoolBytes:     5 * 1024 * 1024 * 1024,
		MaxActiveSessions: 100,
		MaxSessionBytes:   1 * 1024 * 1024,
	}
}
