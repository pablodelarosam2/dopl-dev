// Package integration_test contains end-to-end tests for the record-agent.
// Tests in this package spin up real infrastructure via docker-compose and
// verify observable behaviour (files on disk, objects in S3, metrics endpoint).
package integration_test

import "testing"

func TestAgentStartsAndRespondsToHealthProbe(t *testing.T) {
	t.Skip("TODO: implement e2e health probe test")
}
