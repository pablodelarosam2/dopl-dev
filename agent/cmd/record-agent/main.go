// record-agent is the entrypoint for the dopl DaemonSet agent.
//
// Responsibilities (to be implemented):
//   - Load configuration from environment / config file.
//   - Start the ingest server (Unix socket / gRPC) to receive fixture data
//     emitted by sim_sdk running inside application containers on the same node.
//   - Manage per-request session lifecycle and disk spool.
//   - Upload completed session bundles to S3.
//   - Expose Prometheus metrics and HTTP health/readiness probes.
package main

func main() {
	// Load configuration
	config := config.NewConfig()
	// Start the ingest server
	ingest.Start(config)
	// Manage per-request session lifecycle
	session.Start(config)
	// Spool the records to disk
	spool.Start(config)
	// Upload the completed session bundles to S3
	uploader.Start(config)
	health.Start(config)
}
