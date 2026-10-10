#!/bin/sh
# Run at boot by ALServiceManager (manifest.xml, autorun="true"): start the bridge deployed in ~/pepper_bridge,
# under its watchdog (restarts it if it exits or stops answering; issue #15). Bridges deployed before 0.7 have no
# watchdog.sh: then launch.sh, as before.
DIR="${HOME:-/home/nao}/pepper_bridge"
[ -f "$DIR/watchdog.sh" ] && exec /bin/sh "$DIR/watchdog.sh"
exec /bin/sh "$DIR/launch.sh"
