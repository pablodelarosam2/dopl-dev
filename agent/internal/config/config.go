// Package config loads and validates agent configuration from environment
// variables and an optional config file.
package config

type Config struct {
    // Server
    ListenAddress string        // e.g. "127.0.0.1:7777"

    // Spool
    SpoolDir      string        // e.g. "/var/lib/record-agent/spool"
    MaxSpoolBytes int64         // e.g. 5GB
    MaxSpoolAge   time.Duration // optional cleanup

    // Session limits
    MaxActiveSessions int
    MaxSessionBytes   int64
    MaxSessionAge     time.Duration

    // Ingest limits
    MaxEventBytes   int64
    MaxBatchBytes   int64
    IngestQueueSize int

    // General
    FlushInterval   time.Duration
    LogLevel        string
}

func getEnv(key, defaultValue string) string {
	value := os.Getenv(key)
	if value == "" {
		return defaultValue
	}
	return value
}

func parseInt64(key string, defaultValue int64) int64 {
	value, err := strconv.ParseInt(getEnv(key, strconv.FormatInt(defaultValue, 10)), 10, 64)
	if err != nil {
		return defaultValue
	}
	return value
}

func parseInt(key string, defaultValue int) int {
	value, err := strconv.Atoi(getEnv(key, strconv.Itoa(defaultValue)))
	if err != nil {
		return defaultValue
	}
	return value
}

func parseDuration(key string, defaultValue time.Duration) time.Duration {
	value, err := time.ParseDuration(getEnv(key, defaultValue.String()))
	if err != nil {
		return defaultValue
	}
	return value
}

func (c *Config) Validate() error {
    if c.SpoolDir == "" {
        return errors.New("spool directory cannot be empty")
    }

    if c.MaxSpoolBytes <= 0 {
        return errors.New("max spool bytes must be > 0")
    }

    if c.MaxSessionBytes <= 0 || c.MaxSessionBytes > c.MaxSpoolBytes {
        return errors.New("invalid session byte limit")
    }

    if c.MaxActiveSessions <= 0 {
        return errors.New("max active sessions must be > 0")
    }

    return nil
}

func Load() (*Config, error) {
    cfg := &Config{
        ListenAddress: getEnv("AGENT_LISTEN", "127.0.0.1:7777"),

        SpoolDir:      getEnv("AGENT_SPOOL_DIR", "/tmp/record-agent"),
        MaxSpoolBytes: parseInt64("AGENT_MAX_SPOOL_BYTES", 5*1024*1024*1024),

        MaxActiveSessions: int(parseInt64("AGENT_MAX_ACTIVE_SESSIONS", 1000)),
        MaxSessionBytes:   parseInt64("AGENT_MAX_SESSION_BYTES", 1*1024*1024),
        MaxSessionAge:     time.Duration(parseInt64("AGENT_MAX_SESSION_AGE_MS", 60000)) * time.Millisecond,

        MaxEventBytes:   parseInt64("AGENT_MAX_EVENT_BYTES", 256*1024),
        IngestQueueSize: int(parseInt64("AGENT_INGEST_QUEUE_SIZE", 10000)),

        FlushInterval: time.Duration(parseInt64("AGENT_FLUSH_INTERVAL_MS", 2000)) * time.Millisecond,

        LogLevel: getEnv("AGENT_LOG_LEVEL", "info"),
    }

    if err := cfg.Validate(); err != nil {
        return nil, err
    }

    return cfg, nil
}