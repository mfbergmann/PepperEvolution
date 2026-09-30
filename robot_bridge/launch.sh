#!/bin/sh
# Starts the Pepper bridge in the foreground. The one way the bridge is started:
#   - deploy.py runs it in the background after an upload or --restart;
#   - at boot, ALServiceManager runs it through the pepper-bridge-autostart package
#     (deploy.py --install-autostart).
# Settings come from bridge.env next to this file, written by deploy.py (PORT, API_KEY) or by hand
# (PYTHON, NAOQI_PYTHONPATH, NAOQI_LD_LIBRARY_PATH for NAOqi's desktop build).
DIR=$(cd "$(dirname "$0")" && pwd)
PORT=8888
API_KEY=
PYTHON=python
NAOQI_PYTHONPATH=/opt/aldebaran/lib/python2.7/site-packages
NAOQI_LD_LIBRARY_PATH=
[ -f "$DIR/bridge.env" ] && . "$DIR/bridge.env"
cd "$DIR" || exit 1
if pgrep -f '[p]epper_bridge.py --port' > /dev/null; then
    echo "pepper bridge already running; not starting a second one"
    exit 0
fi
echo $$ > bridge.pid
export PYTHONPATH="$NAOQI_PYTHONPATH${PYTHONPATH:+:$PYTHONPATH}"
if [ -n "$NAOQI_LD_LIBRARY_PATH" ]; then
    export LD_LIBRARY_PATH="$NAOQI_LD_LIBRARY_PATH${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
exec $PYTHON pepper_bridge.py --port="$PORT" ${API_KEY:+--api-key="$API_KEY"} > bridge.log 2>&1 < /dev/null
