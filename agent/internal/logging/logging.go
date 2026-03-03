// Package logging configures the structured logger used throughout the agent.
package logging

import (
	"io"
	"log/slog"
	"os"
)

// New returns a JSON-structured logger writing to stdout at the given level.
// Accepted level strings: "debug", "info", "warn", "error". Anything else
// defaults to Info, so a misconfigured AGENT_LOG_LEVEL never silently breaks
// startup.
func New(level string) *slog.Logger {
	return NewWithWriter(level, os.Stdout)
}

// NewWithWriter returns a JSON-structured logger writing to w. Useful in tests
// that want to capture or discard log output without replacing os.Stdout.
func NewWithWriter(level string, w io.Writer) *slog.Logger {
	opts := &slog.HandlerOptions{Level: parseLevel(level)}
	return slog.New(slog.NewJSONHandler(w, opts))
}

func parseLevel(level string) slog.Level {
	switch level {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
