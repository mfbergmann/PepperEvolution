#!/usr/bin/env bash
# A headless virtual Pepper for testing the bridge without the robot.
#
# NAOqi's desktop build (bin/naoqi-bin inside the Choregraphe 2.5 suite) is the same NAOqi that
# runs on the robot, so every method name and signature the bridge uses can be checked against it.
# It has no hardware layer: no sonar/touch/battery values, no camera, no ALAudioDevice, no tablet,
# no animation packages, and ALTextToSpeech only fires events (no sound). See docs/ROADMAP.md,
# "Testing without the robot".
#
# One-time setup (about 350 MB + 170 MB):
#   mkdir -p ~/.local/opt/pepper-sim && cd ~/.local/opt/pepper-sim
#   curl -LO https://community-static.aldebaran.com/resources/2.5.10/Choregraphe/choregraphe-suite-2.5.10.7-linux64.tar.gz
#   tar xzf choregraphe-suite-2.5.10.7-linux64.tar.gz
#   git clone --depth 1 https://github.com/Michdo93/pynaoqi-python2.7-2.5.7.1-linux64.git pynaoqi-2.5.7.1
#   sed -i 's/NAOH25V50.xml/JULIETTEY20MP.xml/' choregraphe-suite-2.5.10.7-linux64/etc/naoqi/ALRobotModel.xml
# plus a Python 2.7 built with --enable-shared --enable-unicode=ucs4 (mise's python@2.7.18 qualifies)
# with tornado==3.1.1 installed, for the bridge.
#
# Usage:
#   scripts/virtual_pepper.sh start     # start NAOqi on tcp://127.0.0.1:9559
#   scripts/virtual_pepper.sh bridge    # start robot_bridge/pepper_bridge.py against it on BRIDGE_PORT
#   scripts/virtual_pepper.sh status
#   scripts/virtual_pepper.sh stop      # stop both
#   PEPPER_VIRTUAL_BRIDGE=http://127.0.0.1:8899 pytest tests/test_virtual_naoqi.py -v

set -euo pipefail

SIM_DIR="${PEPPER_SIM_DIR:-$HOME/.local/opt/pepper-sim}"
SUITE="${CHOREGRAPHE_DIR:-$SIM_DIR/choregraphe-suite-2.5.10.7-linux64}"
PYNAOQI="${PYNAOQI_DIR:-$SIM_DIR/pynaoqi-2.5.7.1}"
PYTHON27="${PYTHON27:-$HOME/.local/share/mise/installs/python/2.7.18/bin/python}"
NAOQI_PORT="${NAOQI_PORT:-9559}"
BRIDGE_PORT="${BRIDGE_PORT:-8899}"
RUN_DIR="$SIM_DIR/run"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$RUN_DIR"

naoqi_pid() { [ -f "$RUN_DIR/naoqi.pid" ] && cat "$RUN_DIR/naoqi.pid" || true; }
bridge_pid() { [ -f "$RUN_DIR/bridge.pid" ] && cat "$RUN_DIR/bridge.pid" || true; }
alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

case "${1:-status}" in
  start)
    if alive "$(naoqi_pid)"; then echo "virtual NAOqi already running (pid $(naoqi_pid))"; exit 0; fi
    [ -x "$SUITE/bin/naoqi-bin" ] || { echo "naoqi-bin not found under $SUITE (see the setup notes in this script)"; exit 1; }
    grep -q JULIETTEY20MP "$SUITE/etc/naoqi/ALRobotModel.xml" || echo "warning: ALRobotModel.xml is not set to the Pepper model (JULIETTEY20MP.xml)"
    export QI_WRITABLE_PATH="$SIM_DIR/state"
    mkdir -p "$QI_WRITABLE_PATH"
    (cd "$SUITE" && nohup ./bin/naoqi-bin --qi-listen-url "tcp://127.0.0.1:$NAOQI_PORT" --qi-log-level 4 \
        > "$RUN_DIR/naoqi.log" 2>&1 < /dev/null & echo $! > "$RUN_DIR/naoqi.pid")
    for _ in $(seq 1 60); do
      if "$SUITE/bin/qicli" info --qi-url "tcp://127.0.0.1:$NAOQI_PORT" > /dev/null 2>&1; then
        echo "virtual Pepper up on tcp://127.0.0.1:$NAOQI_PORT (pid $(naoqi_pid), log $RUN_DIR/naoqi.log)"; exit 0
      fi
      sleep 2
    done
    echo "NAOqi did not come up; see $RUN_DIR/naoqi.log"; exit 1 ;;
  bridge)
    if alive "$(bridge_pid)"; then echo "bridge already running (pid $(bridge_pid))"; exit 0; fi
    [ -d "$PYNAOQI/lib/python2.7/site-packages/qi" ] || { echo "pynaoqi SDK not found under $PYNAOQI"; exit 1; }
    if curl -sf "http://127.0.0.1:$BRIDGE_PORT/health" > /dev/null 2>&1; then
      echo "something already answers on port $BRIDGE_PORT; stop it first"; exit 1
    fi
    (cd "$ROOT" && PYTHONPATH="$PYNAOQI/lib/python2.7/site-packages" LD_LIBRARY_PATH="$PYNAOQI/lib" PYTHONUNBUFFERED=1 \
        nohup "$PYTHON27" robot_bridge/pepper_bridge.py --port="$BRIDGE_PORT" --naoqi="tcp://127.0.0.1:$NAOQI_PORT" \
        --log-level=INFO > "$RUN_DIR/bridge.log" 2>&1 < /dev/null & echo $! > "$RUN_DIR/bridge.pid")
    for _ in $(seq 1 30); do
      alive "$(bridge_pid)" || { echo "bridge exited; see $RUN_DIR/bridge.log"; exit 1; }
      if curl -sf "http://127.0.0.1:$BRIDGE_PORT/health" > /dev/null 2>&1; then
        echo "bridge healthy on http://127.0.0.1:$BRIDGE_PORT (pid $(bridge_pid), log $RUN_DIR/bridge.log)"
        echo "run: PEPPER_VIRTUAL_BRIDGE=http://127.0.0.1:$BRIDGE_PORT pytest tests/test_virtual_naoqi.py -v"; exit 0
      fi
      sleep 2
    done
    echo "bridge did not become healthy; see $RUN_DIR/bridge.log"; exit 1 ;;
  stop)
    for name in bridge naoqi; do
      pid=$([ "$name" = bridge ] && bridge_pid || naoqi_pid)
      if alive "$pid"; then kill "$pid" && echo "stopped $name (pid $pid)"; fi
      rm -f "$RUN_DIR/$name.pid"
    done ;;
  status)
    if alive "$(naoqi_pid)"; then echo "NAOqi: running (pid $(naoqi_pid)), $("$SUITE/bin/qicli" info --qi-url "tcp://127.0.0.1:$NAOQI_PORT" 2>/dev/null | grep -c '^[0-9]' || echo 0) services"; else echo "NAOqi: not running"; fi
    if alive "$(bridge_pid)"; then echo "bridge: running (pid $(bridge_pid)) $(curl -s "http://127.0.0.1:$BRIDGE_PORT/health" | cut -c1-120)"; else echo "bridge: not running"; fi ;;
  *)
    echo "usage: $0 start|bridge|status|stop"; exit 2 ;;
esac
