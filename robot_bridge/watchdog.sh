#!/bin/sh
# Keeps the Pepper bridge running (issue #15). The one way the bridge is started on the robot:
#   - deploy.py runs this in the background after an upload or --restart;
#   - at boot, ALServiceManager runs it through the pepper-bridge-autostart package (autostart/service.sh).
# It starts the bridge with launch.sh and starts it again when it exits, waiting 5 s, then longer after repeated
# failures (up to a minute; back to 5 s once the bridge has run for 5 minutes). It also asks the bridge's /health
# every WATCHDOG_CHECK_EVERY seconds (after WATCHDOG_GRACE seconds, while NAOqi boots) and restarts a bridge that
# has stopped answering for WATCHDOG_FAILURES checks in a row. "deploy.py --stop" creates watchdog.stop first, so a
# deliberate stop is not undone. Restarts are written to watchdog.log.
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1
PORT=8888
PYTHON=python
WATCHDOG_CHECK_EVERY=60
WATCHDOG_GRACE=180
WATCHDOG_FAILURES=3
[ -f "$DIR/bridge.env" ] && . "$DIR/bridge.env"
STOP="$DIR/watchdog.stop"

if [ -f watchdog.pid ] && kill -0 "$(cat watchdog.pid)" 2>/dev/null; then
    echo "watchdog already running"
    exit 0
fi
echo $$ > watchdog.pid
rm -f "$STOP"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> watchdog.log; }

answers() {  # does the bridge answer /health at all? (503 while NAOqi is still starting counts as an answer)
    $PYTHON -c "
import sys, urllib2
try:
    urllib2.urlopen('http://127.0.0.1:$PORT/health', timeout=10)
except urllib2.HTTPError:
    pass
except Exception:
    sys.exit(1)
" 2>/dev/null
}

delay=5
while [ ! -f "$STOP" ]; do
    started=$(date +%s)
    /bin/sh "$DIR/launch.sh" &
    child=$!
    failures=0
    waited=0
    while kill -0 "$child" 2>/dev/null; do
        sleep 5
        waited=$((waited + 5))
        [ -f "$STOP" ] && break
        if [ "$waited" -ge "$WATCHDOG_GRACE" ] && [ $((waited % WATCHDOG_CHECK_EVERY)) -eq 0 ]; then
            if answers; then
                failures=0
            else
                failures=$((failures + 1))
                if [ "$failures" -ge "$WATCHDOG_FAILURES" ]; then
                    log "bridge (pid $child) has not answered /health $failures times in a row; restarting it"
                    kill "$child" 2>/dev/null
                    sleep 10
                    kill -9 "$child" 2>/dev/null
                    break
                fi
            fi
        fi
    done
    wait "$child" 2>/dev/null
    code=$?
    [ -f "$STOP" ] && break
    if pgrep -f '[p]epper_bridge.py --port' > /dev/null; then
        # launch.sh found a bridge already running (started by hand?): watch that one instead of starting another
        log "another bridge is already running; watching it"
        while pgrep -f '[p]epper_bridge.py --port' > /dev/null && [ ! -f "$STOP" ]; do sleep 10; done
        continue
    fi
    ran=$(($(date +%s) - started))
    [ "$ran" -ge 300 ] && delay=5
    log "bridge exited (code $code) after ${ran}s; starting it again in ${delay}s"
    sleep "$delay"
    delay=$((delay * 2))
    [ "$delay" -gt 60 ] && delay=60
done
rm -f watchdog.pid
