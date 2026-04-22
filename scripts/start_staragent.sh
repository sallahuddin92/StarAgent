#!/usr/bin/env bash
set -euo pipefail

# StarAgent wrapper. The underlying runtime is started via start_macagent.sh
# to preserve the validated operational behavior and PID/log locations.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/scripts/start_macagent.sh" "$@"

