#!/usr/bin/env bash
set -euo pipefail

# TODO: implement agent release automation.
# Expected steps:
#   1. Build the Docker image: docker build -t ghcr.io/dopl-dev/record-agent:$TAG agent/
#   2. Push to registry: docker push ghcr.io/dopl-dev/record-agent:$TAG
#   3. Update values.yaml image.tag and commit.

echo "TODO: release_agent not yet implemented"
exit 0
