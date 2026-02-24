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
	// TODO: wire up config, ingest, session, spool, uploader, metrics, health.
}
