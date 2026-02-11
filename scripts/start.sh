#!/usr/bin/env bash
# PepperEvolution v2 - Deploy bridge and start host application.
#
# Usage:
#   ./scripts/start.sh              # deploy bridge + start host
#   ./scripts/start.sh --no-deploy  # start host only (bridge already running)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Load .env if present
if [ -f .env ]; then
    set -a; source .env; set +a
fi

DEPLOY=true
for arg in "$@"; do
    case "$arg" in
        --no-deploy) DEPLOY=false ;;
    esac
done

# Deploy bridge to robot
if [ "$DEPLOY" = true ]; then
    echo "=== Deploying bridge to robot ==="
    python robot_bridge/deploy.py
    echo ""
    echo "Waiting for bridge to stabilize..."
    sleep 3
fi

# Verify bridge is reachable
BRIDGE_URL="http://${PEPPER_IP:-10.0.100.100}:${BRIDGE_PORT:-8888}/health"
echo "=== Checking bridge at $BRIDGE_URL ==="
if curl -sf "$BRIDGE_URL" > /dev/null 2>&1; then
    echo "Bridge is healthy."
else
    echo "WARNING: Bridge not reachable at $BRIDGE_URL"
    echo "Make sure the bridge is running on the robot."
fi

echo ""
echo "=== Starting PepperEvolution host application ==="
python main.py
