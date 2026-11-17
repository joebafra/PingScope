#!/usr/bin/env bash
# PingScope deploy: update the server to the latest version on GitHub.
#
#   sudo ./deploy.sh            update if GitHub has new commits (used by the timer)
#   sudo ./deploy.sh --force    rebuild and restart even if nothing changed
#   sudo ./deploy.sh --status   show current and available version
#   sudo ./deploy.sh --setup    enable the "Update now" button in Admin → Settings → Updates, and exit
#
# The admin panel and this script talk through two files in PingScope's data directory: the panel writes
# update-request (a systemd path unit, pingscope-update.path, then starts this script right away), and this
# script writes deploy-status.json (last check, result, newest version) and last-deploy.json for the panel.
#
# Steps: fetch → (nothing new? stop) → database backup → pull → rebuild/restart
#        → health check → on failure: roll back to the previous version.
# Works for the Docker install and for the systemd (no Docker) install.
set -euo pipefail

# Run from a temporary copy, so `git pull` can safely replace this file while it runs.
if [ -z "${PINGSCOPE_DEPLOY_COPY:-}" ]; then
  tmp=$(mktemp /tmp/pingscope-deploy.XXXXXX)
  cp "$0" "$tmp"; chmod +x "$tmp"
  PINGSCOPE_DEPLOY_COPY=1 PINGSCOPE_DIR="$(cd "$(dirname "$0")" && pwd)" exec "$tmp" "$@"
fi
trap 'rm -f "$0"' EXIT

DIR=${PINGSCOPE_DIR:?}
BRANCH=${PINGSCOPE_BRANCH:-main}
PORT=$(grep -E '^PINGSCOPE_PORT=' "$DIR/.env" 2>/dev/null | cut -d= -f2 || true); PORT=${PORT:-8080}
KEEP_BACKUPS=${PINGSCOPE_KEEP_BACKUPS:-5}
HEALTH_TIMEOUT=${PINGSCOPE_HEALTH_TIMEOUT:-120}
cd "$DIR"

# Never wait for a password or host-key prompt (the timer runs unattended)
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes}"

APPDATA=""; MODE=""; NEW=""; REQUESTED=false
log() { echo "$(date '+%F %T') $*"; }
die() { log "ERROR: $*"; write_status error "$*"; exit 1; }

version_of() {  # __version__ in a commit
  git show "$1:pingscope/__init__.py" 2>/dev/null | sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' | head -1
}

# Status for Admin → Settings → Updates (result: up-to-date, updated, failed, skipped, error, ready)
write_status() {
  [ -n "$APPDATA" ] && [ -d "$APPDATA" ] || return 0
  local button=false auto=false msg=${2:-}
  systemctl is-active --quiet pingscope-update.path 2>/dev/null && button=true
  systemctl is-enabled --quiet pingscope-deploy.timer 2>/dev/null && auto=true
  msg=${msg//\\/\/}; msg=${msg//\"/\'}
  printf '{"checked": %s, "result": "%s", "message": "%s", "available_version": "%s", "available_commit": "%s", "requested": %s, "button": %s, "auto": %s, "mode": "%s"}\n' \
    "$(date +%s)" "$1" "$msg" "$( [ -n "$NEW" ] && version_of "$NEW")" "${NEW:0:7}" "$REQUESTED" "$button" "$auto" "$MODE" \
    > "$APPDATA/deploy-status.json.tmp" && mv "$APPDATA/deploy-status.json.tmp" "$APPDATA/deploy-status.json"
}

# The "Update now" button: a path unit that starts the update service when the admin panel writes update-request.
# Installed (and kept up to date) by this script itself, so existing installs get it with their next update.
setup_button() {
  [ -f /etc/systemd/system/pingscope-deploy.service ] || return 0   # no update service (installed by install.sh)
  local unit=/etc/systemd/system/pingscope-update.path want
  want=$(printf '[Unit]\nDescription=PingScope: start an update requested in the admin panel\n\n[Path]\nPathExists=%s\nUnit=pingscope-deploy.service\n\n[Install]\nWantedBy=paths.target' "$APPDATA/update-request")
  if [ "$(cat "$unit" 2>/dev/null)" != "$want" ]; then
    printf '%s\n' "$want" > "$unit"
    systemctl daemon-reload
    systemctl enable --now pingscope-update.path >/dev/null 2>&1 && log "update button in the admin panel enabled ($unit)"
  fi
  systemctl is-active --quiet pingscope-update.path || systemctl start pingscope-update.path >/dev/null 2>&1 || true
}

[ "$(id -u)" -eq 0 ] || die "run as root (sudo ./deploy.sh)"

if command -v docker >/dev/null 2>&1 && docker compose ps --services 2>/dev/null | grep -qx pingscope; then
  MODE=docker
  APPDATA="$DIR/data"
elif systemctl list-unit-files pingscope.service >/dev/null 2>&1 && [ -d "$DIR/venv" ]; then
  MODE=systemd
  db=$(systemctl show pingscope -p Environment --value | tr ' ' '\n' | grep '^PINGSCOPE_DB=' | cut -d= -f2 || true)
  APPDATA=$(dirname "${db:-/var/lib/pingscope/pingscope.db}")
else
  die "no running PingScope install found in $DIR (docker compose or systemd)"
fi

# An update requested with the button in the admin panel. Taken before the lock, so the path unit doesn't
# start this again and again while another deploy runs.
if [ -f "$APPDATA/update-request" ]; then
  rm -f "$APPDATA/update-request"
  REQUESTED=true
  log "update requested in the admin panel"
fi

# Only one deploy at a time
exec 9>/run/lock/pingscope-deploy.lock 2>/dev/null || exec 9>/tmp/pingscope-deploy.lock
flock -n 9 || { log "another deploy is running; skipping"; exit 0; }

setup_button

git fetch --quiet origin "$BRANCH" || die "git fetch failed (check access to GitHub; for a private fork over SSH: ssh -T git@github.com)"
CUR=$(git rev-parse HEAD)
NEW=$(git rev-parse "origin/$BRANCH")

if [ "${1:-}" = "--setup" ]; then
  write_status ready "update button ready"
  log "update button ready (Admin → Settings → Updates)"
  exit 0
fi

DEPLOYED_FILE="$DIR/data/.deployed-commit"
DEPLOYED=$(cat "$DEPLOYED_FILE" 2>/dev/null || true)

if [ "${1:-}" = "--status" ]; then
  echo "mode:      $MODE"
  echo "code:      $(git log -1 --format='%h %s (%cr)' "$CUR")"
  [ -n "$DEPLOYED" ] && [ "$DEPLOYED" != "$CUR" ] && echo "running:   ${DEPLOYED:0:7} (code was pulled but not deployed; run ./deploy.sh)"
  echo "available: $(git log -1 --format='%h %s (%cr)' "$NEW")"
  [ "$CUR" = "$NEW" ] && echo "up to date" || echo "$(git rev-list --count "$CUR..$NEW") new commit(s)"
  [ -f "$DIR/data/.deploy-failed" ] && echo "last failed deploy: $(cut -c1-7 "$DIR/data/.deploy-failed") (skipped until a newer commit, or use --force)"
  exit 0
fi

if [ "$CUR" = "$NEW" ] && [ "${1:-}" != "--force" ]; then
  # Code is current. But if someone ran `git pull` by hand, the running version may be older: rebuild then.
  if [ "$DEPLOYED" = "$CUR" ]; then
    write_status up-to-date ""
    [ "$REQUESTED" = true ] && log "already up to date (${CUR:0:7})"
    exit 0   # nothing to do (silent, the timer runs this every few minutes)
  fi
  if [ -z "$DEPLOYED" ]; then
    log "running version unknown (first run of this deploy script); rebuilding ${CUR:0:7} once"
  else
    log "code was updated by hand (git pull) but not deployed yet; deploying ${CUR:0:7}"
  fi
fi
FAILED_FILE="$DIR/data/.deploy-failed"
if [ "${1:-}" != "--force" ] && [ -f "$FAILED_FILE" ] && [ "$(cat "$FAILED_FILE")" = "$NEW" ]; then
  write_status skipped "version ${NEW:0:7} failed to start before; waiting for a newer version (or run sudo ./deploy.sh --force)"
  exit 0   # this version failed before; wait for a newer commit (or run with --force)
fi

log "deploying ${CUR:0:7} → ${NEW:0:7} ($MODE)"
git log --format='  %h %s' "$CUR..$NEW" 2>/dev/null | head -20 || true

# 1. Backup the database before anything changes
mkdir -p data/backups
backup="data/backups/pre-deploy-$(date +%Y%m%d-%H%M%S)-${CUR:0:7}.db"
if [ "$MODE" = docker ]; then
  docker exec pingscope python -m pingscope backup "/data/backups/$(basename "$backup")" >/dev/null 2>&1 \
    && log "database backup: $backup" || log "warning: database backup failed (continuing)"
else
  db=$(systemctl show pingscope -p Environment --value | tr ' ' '\n' | grep '^PINGSCOPE_DB=' | cut -d= -f2)
  sudo -u pingscope env PINGSCOPE_DB="${db:-/var/lib/pingscope/pingscope.db}" "$DIR/venv/bin/python" -m pingscope backup "/var/lib/pingscope/$(basename "$backup")" >/dev/null 2>&1 \
    && log "database backup: /var/lib/pingscope/$(basename "$backup")" || log "warning: database backup failed (continuing)"
fi
# (|| true: without any backup ls fails, and with pipefail that would end the deploy without a message)
ls -1t data/backups/pre-deploy-*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f || true

start() {
  if [ "$MODE" = docker ]; then
    # the commit shown next to the version: the last one that changes more than CHANGELOG.md (see CONTRIBUTING.md)
    PINGSCOPE_COMMIT="$(git log -1 --format=%h -- . ':(exclude)CHANGELOG.md')" docker compose up -d --build --remove-orphans 9>&-
  else
    "$DIR/venv/bin/pip" install -q -r requirements.txt 9>&-
    systemctl restart pingscope 9>&-
  fi
}

healthy() {
  for _ in $(seq 1 $((HEALTH_TIMEOUT / 2))); do
    curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/session" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

# 2. Update the code (fast-forward only; .env and data/ are not tracked by git)
git merge --ff-only --quiet "origin/$BRANCH" || die "can't fast-forward: local changes in $DIR? (git status)"

# 3. Rebuild/restart and check it comes up
if start && healthy; then
  rm -f "$FAILED_FILE"
  echo "$NEW" > "$DEPLOYED_FILE"
  log "deploy OK: now running ${NEW:0:7}"
  printf '{"at": %s, "from_commit": "%s", "from_version": "%s", "to_commit": "%s", "to_version": "%s"}\n' \
    "$(date +%s)" "${CUR:0:7}" "$(version_of "$CUR")" "${NEW:0:7}" "$(version_of "$NEW")" > "$APPDATA/last-deploy.json" || true
  write_status updated "now running ${NEW:0:7}"
  [ "$MODE" = docker ] && docker image prune -f >/dev/null 2>&1 || true
  exit 0
fi

# 4. Roll back
ROLLBACK=$CUR
if [ -n "$DEPLOYED" ] && [ "$DEPLOYED" != "$NEW" ] && git cat-file -e "$DEPLOYED^{commit}" 2>/dev/null; then ROLLBACK=$DEPLOYED; fi
log "new version did not start; rolling back to ${ROLLBACK:0:7}"
echo "$NEW" > "$FAILED_FILE"
git reset --hard --quiet "$ROLLBACK"
if start && healthy; then
  echo "$ROLLBACK" > "$DEPLOYED_FILE"
  die "deploy of ${NEW:0:7} failed; rolled back to ${ROLLBACK:0:7} (database backup: $backup)"
fi
die "deploy failed AND rollback did not come up; check: docker logs pingscope / journalctl -u pingscope"
