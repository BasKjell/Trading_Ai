#!/usr/bin/env bash
# Start agentgateway for local agent roles (T-025) on loopback only.
# agentgateway writes a generated session key back into its config file, so it runs a private copy
# (mode 0600 in a 0700 directory) instead of the versioned config/agentgateway.yaml.
# Usage: scripts/agent_gateway.sh [--check]
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
binary="${AGENTGATEWAY_BIN:-$(command -v agentgateway || echo "$HOME/.local/bin/agentgateway")}"
state="${TRADING_AGW_STATE:-$HOME/.trading-ai/agentgateway}"
[[ -x "$binary" ]] || { echo "agentgateway not found (install v1.7.0, see docs/DEV_ORCHESTRATOR.md)" >&2; exit 2; }
"$binary" --version | grep -q '"version": "1.7.0"' || { echo "expected agentgateway 1.7.0" >&2; exit 2; }
umask 077
mkdir -p "$state"
chmod 700 "$state"
install -m 600 "$root/config/agentgateway.yaml" "$state/config.yaml"
if [[ "${1:-}" == "--check" ]]; then
  "$binary" -f "$state/config.yaml" --validate-only
  exit $?
fi
exec "$binary" -f "$state/config.yaml"
