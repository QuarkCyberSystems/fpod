#!/usr/bin/env bash
# End-to-end smoke test for fpod.
#
# Exercises the full lifecycle on a throwaway bench named `fpodsmoke`:
#   init (idempotent) → create → list → curl 200 → shell exec → code --print
#   → logs → stop → start → restart → delete --yes → list shows it gone
#
# Optional: set SMOKE_INSTALL=1 to also test install-app/migrate/backup
# against ERPNext. Adds ~10 min.
#
# Usage:
#   bash tests/test_smoke.sh           # ~12 min
#   SMOKE_INSTALL=1 bash tests/test_smoke.sh  # ~22 min

set -euo pipefail

FPOD="${FPOD:-fpod}"
BENCH="${SMOKE_BENCH:-fpodsmoke}"
SITE="${BENCH}.localhost"

# Resolve port from the loaded config so we curl the right URL.
PORT="$("$FPOD" version >/dev/null && python3 -c "
import tomllib
from pathlib import Path
print(tomllib.loads(Path.home().joinpath('.fpod/config.toml').read_text())['fpod']['host_port'])
")"

URL="http://${SITE}:${PORT}"

step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
expect_200() {
  local got
  got=$(curl -sI -o /dev/null -w '%{http_code}' "$1")
  [ "$got" = "200" ] || fail "expected 200 from $1, got $got"
  ok "$1 → 200"
}
expect_status_not_200() {
  local got
  got=$(curl -sI -o /dev/null -w '%{http_code}' "$1" || true)
  [ "$got" != "200" ] || fail "expected non-200 from $1, got 200"
  ok "$1 → $got (as expected after stop)"
}

# ---------------------------------------------------------------------------

step "ensure init has been run"
"$FPOD" doctor >/dev/null || fail "fpod doctor failed — run 'fpod init' first"
ok "doctor reports stack is healthy"

step "pre-clean any leftover bench named $BENCH"
"$FPOD" delete "$BENCH" --yes >/dev/null 2>&1 || true

step "create $BENCH"
"$FPOD" create "$BENCH"
ok "created"

step "list shows the bench"
"$FPOD" list | grep -q "$BENCH" || fail "$BENCH not in list output"
ok "list contains $BENCH"

step "site responds 200"
expect_200 "$URL/login"

step "shell exec inside container"
podman exec "frappe-$BENCH" bash -lc 'cd /workspace/frappe-bench && bench --site '"$SITE"' list-apps' \
  | grep -q '^frappe' || fail "shell exec didn't show frappe app"
ok "bench list-apps reachable via container exec"

step "code --print emits vscode-remote URI"
"$FPOD" code "$BENCH" --print | grep -q '^vscode-remote://attached-container+' \
  || fail "code --print didn't return a vscode-remote URI"
ok "URI emitted"

step "logs (non-follow) returns recent output"
"$FPOD" logs "$BENCH" 2>&1 | grep -q 'Running on http' || fail "expected 'Running on http' in logs"
ok "logs contain honcho's web-ready marker"

step "logs --service web filters honcho prefix"
"$FPOD" logs "$BENCH" --service web 2>&1 | head -5 | grep -q ' web\.' || fail "service-filtered logs missing web.* prefix"
ok "web-only filter works"

step "stop"
"$FPOD" stop "$BENCH" >/dev/null
expect_status_not_200 "$URL/login"

step "start (re-wait for ready)"
"$FPOD" start "$BENCH" >/dev/null
expect_200 "$URL/login"

step "restart"
"$FPOD" restart "$BENCH" >/dev/null
expect_200 "$URL/login"

# ---------------------------------------------------------------------------

if [ "${SMOKE_INSTALL:-0}" = "1" ]; then
  step "install-app erpnext"
  "$FPOD" install-app "$BENCH" erpnext --branch version-15
  ok "install-app finished"

  step "list-apps shows erpnext"
  podman exec "frappe-$BENCH" bash -lc 'cd /workspace/frappe-bench && bench --site '"$SITE"' list-apps' \
    | grep -q '^erpnext' || fail "erpnext not in list-apps"
  ok "erpnext present on site"

  step "install-app is idempotent"
  "$FPOD" install-app "$BENCH" erpnext >/dev/null
  ok "rerun completed without error"

  step "migrate"
  "$FPOD" migrate "$BENCH" >/dev/null 2>&1 || fail "migrate failed"
  ok "migrate clean"

  step "backup"
  out_dir="/tmp/fpod-smoke-backup-$$"
  "$FPOD" backup "$BENCH" --out "$out_dir" >/dev/null
  ls "$out_dir"/*database.sql.gz >/dev/null || fail "no database.sql.gz produced"
  ok "backup at $out_dir"
  rm -rf "$out_dir"
fi

# ---------------------------------------------------------------------------

step "delete (--yes, full wipe)"
"$FPOD" delete "$BENCH" --yes

step "list no longer shows the bench"
if "$FPOD" list 2>&1 | grep -q "$BENCH"; then
  fail "$BENCH still in list after delete"
fi
ok "$BENCH gone"

step "bench dir removed from disk"
[ ! -d "$HOME/.fpod/benches/$BENCH" ] || fail "$HOME/.fpod/benches/$BENCH still exists"
ok "no on-disk remnants"

printf '\n\033[1;32mSMOKE TEST PASSED\033[0m\n'
