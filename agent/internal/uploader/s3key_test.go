package uploader

import (
	"testing"
	"time"
)

func TestBuildEndpointKey(t *testing.T) {
	tests := []struct {
		method string
		path   string
		want   string
	}{
		{"POST", "/quote", "post_quote"},
		{"GET", "/checkout/status", "get_checkout_status"},
		{"GET", "/", "get"},
		{"PUT", "/api/v1/users/", "put_api_v1_users"},
		{"POST", "/Quote", "post_quote"},
		{"post", "/quote", "post_quote"},
		{"DELETE", "/items", "delete_items"},
	}
	for _, tt := range tests {
		got := buildEndpointKey(tt.method, tt.path)
		if got != tt.want {
			t.Errorf("buildEndpointKey(%q, %q) = %q, want %q",
				tt.method, tt.path, got, tt.want)
		}
	}
}

func TestStructuredS3Key(t *testing.T) {
	tests := []struct {
		service   string
		method    string
		path      string
		fixtureID string
		createdAt time.Time
		want      string
	}{
		{
			"pricing-api", "POST", "/quote", "abc-123",
			time.Date(2026, 3, 21, 14, 30, 0, 0, time.UTC),
			"fixtures/pricing-api/post_quote/2026-03-21/abc-123.json",
		},
		{
			"my-svc", "GET", "/checkout/status", "fix-001",
			time.Date(2026, 1, 15, 0, 0, 0, 0, time.UTC),
			"fixtures/my-svc/get_checkout_status/2026-01-15/fix-001.json",
		},
		{
			"svc", "GET", "/", "id-1",
			time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC),
			"fixtures/svc/get/2026-06-01/id-1.json",
		},
		{
			"svc", "PUT", "/users/", "id-2",
			time.Date(2026, 12, 31, 0, 0, 0, 0, time.UTC),
			"fixtures/svc/put_users/2026-12-31/id-2.json",
		},
	}
	for _, tt := range tests {
		got := structuredS3Key(tt.service, tt.method, tt.path, tt.fixtureID, tt.createdAt)
		if got != tt.want {
			t.Errorf("structuredS3Key(%q, %q, %q, %q, %v) = %q, want %q",
				tt.service, tt.method, tt.path, tt.fixtureID, tt.createdAt, got, tt.want)
		}
	}
}

func TestStructuredS3KeyFallback(t *testing.T) {
	// When service, method, or path is empty, falls back to flat key format
	ts := time.Date(2026, 3, 21, 0, 0, 0, 0, time.UTC)

	tests := []struct {
		name      string
		service   string
		method    string
		path      string
		fixtureID string
		prefix    string
		want      string
	}{
		{
			"empty service falls back",
			"", "POST", "/quote", "fix-1", "fixtures",
			"fixtures/fix-1/fixture.json",
		},
		{
			"empty method falls back",
			"svc", "", "/quote", "fix-1", "fixtures",
			"fixtures/fix-1/fixture.json",
		},
		{
			"empty path falls back",
			"svc", "POST", "", "fix-1", "fixtures",
			"fixtures/fix-1/fixture.json",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := structuredS3Key(tt.service, tt.method, tt.path, tt.fixtureID, ts)
			wantFallback := s3Key(tt.prefix, tt.fixtureID)
			if got != wantFallback {
				t.Errorf("got %q, want fallback %q", got, wantFallback)
			}
		})
	}
}
