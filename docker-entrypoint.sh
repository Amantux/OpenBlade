#!/bin/sh
# Entrypoint: fix /data ownership if running as root, then drop to openblade user.
# This handles Docker volumes that mount over the image's pre-configured /data directory.
if [ "$(id -u)" = "0" ]; then
    chown -R openblade:openblade /data 2>/dev/null || true
    exec gosu openblade "$@"
fi
exec "$@"
