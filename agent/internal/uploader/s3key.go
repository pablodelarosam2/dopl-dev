package uploader

import (
	"fmt"
	"regexp"
	"strings"
	"time"
)

// underscoreRun matches one or more consecutive underscores for collapsing.
var underscoreRun = regexp.MustCompile(`_+`)

// buildEndpointKey slugifies an HTTP method + path into a stable endpoint key.
//
// Rules (matches Python build_endpoint_key in sim_sdk/fixture_uploader.py):
//   - Lowercase
//   - Slashes replaced with underscores
//   - Consecutive underscores collapsed to one
//   - Leading/trailing underscores stripped
//
// Examples:
//
//	buildEndpointKey("POST", "/quote")          -> "post_quote"
//	buildEndpointKey("GET", "/checkout/status")  -> "get_checkout_status"
func buildEndpointKey(method, path string) string {
	raw := strings.ToLower(method + "_" + path)
	raw = strings.ReplaceAll(raw, "/", "_")
	raw = underscoreRun.ReplaceAllString(raw, "_")
	raw = strings.Trim(raw, "_")
	return raw
}

// structuredS3Key builds the full S3 object key using the Phase 3 prefix layout:
//
//	fixtures/{service}/{endpoint_key}/{date}/{fixture_id}.json
//
// If any required metadata (service, method, path) is missing, it falls back to
// the legacy flat key format for backward compatibility.
func structuredS3Key(service, method, path, fixtureID string, createdAt time.Time) string {
	if service == "" || method == "" || path == "" {
		// Fallback to legacy flat key when metadata is incomplete.
		// This preserves backward compatibility with Phase 2 uploads.
		return s3Key("fixtures", fixtureID)
	}
	endpointKey := buildEndpointKey(method, path)
	dateStr := createdAt.Format("2006-01-02")
	return fmt.Sprintf("fixtures/%s/%s/%s/%s.json", service, endpointKey, dateStr, fixtureID)
}
