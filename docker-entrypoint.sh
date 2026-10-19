#!/bin/sh
# Railway (and most hosts) mount volumes root-owned. Started as root, give the
# asset directory to the sidecar user, then run the server without root.
set -eu
if [ "$(id -u)" = 0 ]; then
  if [ -n "${ASSET_DIR:-}" ]; then
    mkdir -p "$ASSET_DIR"
    chown sidecar:sidecar "$ASSET_DIR"  # files inside are created by sidecar
  fi
  exec setpriv --reuid=sidecar --regid=sidecar --init-groups -- "$@"
fi
exec "$@"
