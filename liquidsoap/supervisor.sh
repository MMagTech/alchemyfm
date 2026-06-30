#!/bin/sh
set -eu

DATA_DIR="${DATA_DIR:-/data}"
LIQUIDSOAP_DIR="$DATA_DIR/liquidsoap"
MANIFEST="$LIQUIDSOAP_DIR/manifest.json"
POLL_SEC="${SUPERVISOR_POLL_SEC:-3}"
HASH_DIR="/tmp/ls-supervisor-hashes"

mkdir -p "$HASH_DIR"

log() {
  printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

hash_file() {
  sha256sum "$1" 2>/dev/null | awk '{print $1}'
}

stop_slug() {
  slug="$1"
  pidfile="$HASH_DIR/$slug.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      log "Stopping station $slug (pid $pid)"
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile" "$HASH_DIR/$slug.hash"
  fi
}

start_slug() {
  slug="$1"
  script_rel="$2"
  script="$LIQUIDSOAP_DIR/$script_rel"
  pidfile="$HASH_DIR/$slug.pid"
  hashfile="$HASH_DIR/$slug.hash"

  if [ ! -f "$script" ]; then
    log "Station $slug: script missing at $script"
    stop_slug "$slug"
    return
  fi

  new_hash="$(hash_file "$script")"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      old_hash=""
      [ -f "$hashfile" ] && old_hash="$(cat "$hashfile")"
      if [ "$new_hash" = "$old_hash" ]; then
        return
      fi
      log "Station $slug: script changed, restarting"
      stop_slug "$slug"
    else
      log "Station $slug exited, restarting"
      rm -f "$pidfile" "$hashfile"
    fi
  fi

  log "Starting station $slug from $script"
  liquidsoap "$script" &
  echo "$!" >"$pidfile"
  echo "$new_hash" >"$hashfile"
}

reconcile() {
  [ -f "$MANIFEST" ] || return 0

  desired_file="$HASH_DIR/desired.slugs"
  : >"$desired_file"

  # Parse manifest with liquidsoap's embedded python if available, else use awk on JSON
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$MANIFEST" "$desired_file" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
out = open(sys.argv[2], "w")
for entry in manifest.get("stations") or []:
    if entry.get("enabled") and entry.get("slug") and entry.get("script"):
        out.write(f"{entry['slug']}\t{entry['script']}\n")
PY
  else
    # Minimal fallback: enabled stations only (requires jq)
    if command -v jq >/dev/null 2>&1; then
      jq -r '.stations[] | select(.enabled) | "\(.slug)\t\(.script)"' "$MANIFEST" >"$desired_file"
    else
      log "ERROR: need python3 or jq to read manifest"
      return 1
    fi
  fi

  # Stop stations not in desired set
  for pidfile in "$HASH_DIR"/*.pid; do
    [ -e "$pidfile" ] || continue
    slug="$(basename "$pidfile" .pid)"
    if ! grep -q "^${slug}	" "$desired_file" 2>/dev/null; then
      stop_slug "$slug"
    fi
  done

  while IFS='	' read -r slug script; do
    [ -n "$slug" ] || continue
    start_slug "$slug" "$script"
  done <"$desired_file"
}

shutdown() {
  log "Supervisor shutting down"
  for pidfile in "$HASH_DIR"/*.pid; do
    [ -e "$pidfile" ] || continue
    stop_slug "$(basename "$pidfile" .pid)"
  done
  exit 0
}

trap shutdown INT TERM

log "Liquidsoap supervisor watching $MANIFEST"
while true; do
  reconcile || true
  sleep "$POLL_SEC"
done
