#!/usr/bin/env bash
# One-click deploy: copy this checkout to the live copy and restart the service.
#   scripts/deploy.sh            deploy
#   scripts/deploy.sh --dry-run  show what would change, touch nothing
# The live service runs from LIVE (not from this checkout). Before changing anything the script saves the database
# and the current code under LIVE/backups; if the new code does not come up healthy it puts the old code back.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE="${INBOX_TEND_LIVE:-/srv/inboxtend}"
UNIT="${INBOX_TEND_UNIT:-inboxtend}"
PORT="${INBOX_TEND_PORT:-8765}"
DB="${INBOX_TEND_DB:-/var/lib/inboxtend/data/tasks.sqlite3}"
PY="$LIVE/.venv/bin/python"
STAMP="$(date +%Y%m%d-%H%M%S)"
# Never overwritten or removed on the live side: secrets, data, the virtualenv and old releases/backups.
EXCLUDES=(--exclude=.git --exclude=.env --exclude='.env.*' --exclude=.venv --exclude=node_modules --exclude=__pycache__
          --exclude='*.pyc' --exclude=data --exclude=backups --exclude=releases --exclude=current --exclude=rollback
          --exclude=tests --exclude=frontend --exclude='*.sqlite3*' --exclude=client_secret'*'.json --exclude=credentials'*'.json
          --exclude=.tasks --exclude=.DS_Store)

[ "$(id -u)" = 0 ] || { echo "Run as root (it writes to $LIVE and restarts $UNIT)." >&2; exit 1; }
[ -x "$PY" ] || { echo "No Python at $PY: is $LIVE the live copy?" >&2; exit 1; }

echo "== 1/5 Checking the code compiles"
"$PY" - "$SRC" <<'PYEOF'
import sys, pathlib
bad = 0
for p in pathlib.Path(sys.argv[1]).rglob('*.py'):
    if any(x in p.parts for x in ('.venv', 'node_modules', '__pycache__')): continue
    try: compile(p.read_text(), str(p), 'exec')
    except SyntaxError as e: print('  syntax error:', p, e); bad += 1
sys.exit(1 if bad else 0)
PYEOF

if [ "${1:-}" = "--dry-run" ]; then
  echo "== Dry run: files that would change"
  rsync -rlptic --delete --dry-run "${EXCLUDES[@]}" "$SRC/" "$LIVE/" | grep -v '^\.' || echo "  (nothing)"
  exit 0
fi

echo "== 2/5 Saving the database and the current code to $LIVE/backups"
mkdir -p "$LIVE/backups"
if [ -f "$DB" ]; then
  "$PY" - "$DB" "$LIVE/backups/tasks-pre-deploy-$STAMP.sqlite3" <<'PYEOF'
import sqlite3, sys, os
src = sqlite3.connect('file:%s?mode=ro' % sys.argv[1], uri=True); dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
dst.close(); src.close(); os.chmod(sys.argv[2], 0o600)
PYEOF
fi
CODE_TAR="$LIVE/backups/code-pre-deploy-$STAMP.tar.gz"
tar -C "$LIVE" -czf "$CODE_TAR" --exclude=./.venv --exclude=./backups --exclude=./releases --exclude=./rollback --exclude=./data --exclude=./.env --exclude=./node_modules --exclude=./__pycache__ .
chmod 600 "$CODE_TAR"
ls -1t "$LIVE"/backups/tasks-pre-deploy-*.sqlite3 2>/dev/null | tail -n +6 | xargs -r rm -f   # keep the last 5 of each
ls -1t "$LIVE"/backups/code-pre-deploy-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f

echo "== 3/5 Copying the new code"
rsync -rlptic --delete "${EXCLUDES[@]}" "$SRC/" "$LIVE/" | grep -v '^\.' || echo "  (no changes)"

echo "== 4/5 Restarting $UNIT"
systemctl daemon-reload
systemctl restart "$UNIT"

echo "== 5/5 Waiting for it to answer"
healthy() { curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; }
for _ in $(seq 1 30); do systemctl is-active --quiet "$UNIT" && healthy && { echo "Deployed $STAMP: $UNIT is healthy on port $PORT."; exit 0; }; sleep 1; done

echo "The new code did not come up healthy: putting the previous code back." >&2
journalctl -u "$UNIT" -n 15 --no-pager >&2 || true
tar -C "$LIVE" -xzf "$CODE_TAR"
systemctl restart "$UNIT"
for _ in $(seq 1 20); do healthy && { echo "Rolled back; $UNIT is healthy again. Fix the problem and deploy again." >&2; exit 1; }; sleep 1; done
echo "Rollback did not help either. Code backup: $CODE_TAR" >&2
exit 2
