#!/usr/bin/env bash
# PepperEvolution v2 - Deploy bridge and start host application.
#
# Usage:
#   ./scripts/start.sh              # deploy bridge + start host
#   ./scripts/start.sh --no-deploy  # start host only (bridge already running)
#   ./scripts/start.sh --fake       # no robot: fake bridge, real AI, web UI
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Prefer a project virtualenv if present
if [ -x ".venv/bin/python" ]; then PYTHON=".venv/bin/python"
elif [ -x "venv/bin/python" ]; then PYTHON="venv/bin/python"
else PYTHON="${PYTHON:-python3}"; fi

# Load .env if present
if [ -f .env ]; then
    set -a; source .env; set +a
fi

DEPLOY=true
for arg in "$@"; do
    case "$arg" in
        --no-deploy) DEPLOY=false ;;
        --fake) DEPLOY=false; export PEPPER_FAKE_BRIDGE=true ;;
    esac
done

if [ "${PEPPER_FAKE_BRIDGE:-false}" = "true" ]; then
    echo "=== Fake bridge mode: no robot will be contacted ==="
elif [ "$DEPLOY" = true ]; then
    echo "=== Deploying bridge to robot ==="
    "$PYTHON" robot_bridge/deploy.py
else
    BRIDGE_URL="http://${PEPPER_IP:-10.0.100.100}:${BRIDGE_PORT:-8888}/health"
    echo "=== Checking bridge at $BRIDGE_URL ==="
    if curl -sf "$BRIDGE_URL" > /dev/null 2>&1; then
        echo "Bridge is healthy."
    else
        echo "WARNING: Bridge not reachable at $BRIDGE_URL"
        echo "Start it with: $PYTHON robot_bridge/deploy.py"
    fi
fi

echo ""
echo "=== Starting PepperEvolution host application ==="
exec "$PYTHON" main.py
