#!/bin/sh
set -eu

PUID="${PUID:-99}"
PGID="${PGID:-100}"

if [ "$PUID" = "0" ]; then
    exec python -m uvicorn src.web.app:app --host 0.0.0.0 --port 8000
fi

if getent group "$PGID" >/dev/null 2>&1; then
    GROUP_NAME="$(getent group "$PGID" | cut -d: -f1)"
else
    GROUP_NAME="magazine"
    groupadd -g "$PGID" "$GROUP_NAME"
fi

if getent passwd "$PUID" >/dev/null 2>&1; then
    USER_NAME="$(getent passwd "$PUID" | cut -d: -f1)"
else
    USER_NAME="magazine"
    useradd -u "$PUID" -g "$GROUP_NAME" -M -d /nonexistent -s /usr/sbin/nologin "$USER_NAME"
fi

mkdir -p /config
chown -R "$PUID:$PGID" /config

exec gosu "$PUID:$PGID" python -m uvicorn src.web.app:app --host 0.0.0.0 --port 8000
