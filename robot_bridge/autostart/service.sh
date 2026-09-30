#!/bin/sh
# Run at boot by ALServiceManager (manifest.xml, autorun="true"): start the bridge deployed in ~/pepper_bridge.
exec /bin/sh "${HOME:-/home/nao}/pepper_bridge/launch.sh"
