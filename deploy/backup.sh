#!/usr/bin/env bash
# Back up SQLite, uploads, Chroma and encryption keys.
#
# Usage:
#   DATA_DIR=/opt/mediknow/data BACKUP_DIR=/opt/mediknow/backups ./deploy/backup.sh
#   STOP_APP=1 COMPOSE_DIR=/opt/mediknow ./deploy/backup.sh   # consistent copy
#
# Cron example:
#   15 3 * * * STOP_APP=1 COMPOSE_DIR=/opt/mediknow \
#     DATA_DIR=/opt/mediknow/data BACKUP_DIR=/opt/mediknow/backups \
#     /opt/mediknow/deploy/backup.sh >> /var/log/mediknow-backup.log 2>&1
set -euo pipefail

DATA_DIR="${DATA_DIR:-/opt/mediknow/data}"
BACKUP_DIR="${BACKUP_DIR:-/opt/mediknow/backups}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/mediknow}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STOP_APP="${STOP_APP:-0}"
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
APP_STOPPED=0

cleanup() {
  if [ "$APP_STOPPED" = "1" ]; then
    (cd "$COMPOSE_DIR" && docker compose start app >/dev/null 2>&1) || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$BACKUP_DIR"

if [ "$STOP_APP" = "1" ]; then
  (cd "$COMPOSE_DIR" && docker compose stop app)
  APP_STOPPED=1
fi

# SQLite: use the online backup API when available, otherwise copy WAL files too.
backup_sqlite() {
  local source="$1" target="$2"
  if [ ! -f "$source" ]; then
    return 0
  fi
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$source" ".backup '$target'"
  else
    cp "$source" "$target"
    [ -f "$source-wal" ] && cp "$source-wal" "$target-wal"
    [ -f "$source-shm" ] && cp "$source-shm" "$target-shm"
  fi
}

backup_sqlite "$DATA_DIR/app.db" "$WORK/app.db"
backup_sqlite "$DATA_DIR/checkpoints.sqlite" "$WORK/checkpoints.sqlite"

for name in .encryption_key .app_secret; do
  [ -f "$DATA_DIR/$name" ] && cp -a "$DATA_DIR/$name" "$WORK/"
done

for name in uploads chroma; do
  [ -d "$DATA_DIR/$name" ] && cp -a "$DATA_DIR/$name" "$WORK/"
done

# Keep small data files (manifests, eval snapshots) but never nested backups.
find "$DATA_DIR" -maxdepth 1 -type f ! -name 'app.db*' ! -name 'checkpoints.sqlite*' \
  -exec cp -a {} "$WORK/" \;

ARCHIVE="$BACKUP_DIR/mediknow-$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$WORK" .
chmod 600 "$ARCHIVE"

find "$BACKUP_DIR" -maxdepth 1 -name 'mediknow-*.tar.gz' -mtime +"$KEEP_DAYS" -delete
echo "backup written: $ARCHIVE"