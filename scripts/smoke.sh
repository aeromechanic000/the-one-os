#!/usr/bin/env bash
# smoke.sh — start Web OS, exercise login + core API, then stop it.
set -u
PORT="${1:-11687}"
BASE="http://127.0.0.1:$PORT"
COOKIE="$(mktemp)"
LOG="$(mktemp)"
cd "$(dirname "$0")/.."

echo "[smoke] starting server on port $PORT ..."
uv run main.py --port "$PORT" >"$LOG" 2>&1 &
SERVER_PID=$!

fail() { echo "[smoke] FAIL: $1"; kill "$SERVER_PID" 2>/dev/null; exit 1; }

for i in $(seq 1 40); do
  curl -sf "$BASE/auth/login" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -sf "$BASE/auth/login" >/dev/null || fail "login page did not come up"

PASSWORD="$(grep -m1 'Password  :' "$LOG" | sed 's/.*: *//;s/ *$//')"
[ -n "$PASSWORD" ] || { cat "$LOG"; fail "password not printed"; }
echo "[smoke] password captured: ${PASSWORD:0:3}***"

# wrong password rejected
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/json' -d '{"password":"nope"}')
[ "$CODE" = "401" ] || fail "wrong password accepted ($CODE)"

# correct password -> session cookie
curl -sf -c "$COOKIE" -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PASSWORD\"}" >/dev/null || fail "login failed"

check_json() {
  local path="$1" expect="$2"
  local body
  body="$(curl -sf -b "$COOKIE" "$BASE$path")" || fail "GET $path failed"
  echo "$body" | grep -q "$expect" || fail "$path missing '$expect': $body"
  echo "[smoke] OK $path"
}

check_json "/api/session" "remaining_seconds"
check_json "/api/hosts" "\[\]"
check_json "/api/actions" "Add User"
check_json "/api/attention/rules" "sudo password"
check_json "/api/settings" "keyboard_mode"
check_json "/api/transfer/queue" "\["
# staged files endpoint just needs to answer (storage may hold prior runs)
curl -sf -b "$COOKIE" "$BASE/api/transfer/files" >/dev/null || fail "GET /api/transfer/files failed"
echo "[smoke] OK /api/transfer/files"
check_json "/api/files/local?path=" "entries"

# desktop shell renders with I18N + templates
DESKTOP="$(curl -sf -b "$COOKIE" "$BASE/")" || fail "desktop page failed"
echo "$DESKTOP" | grep -q "window.I18N" || fail "I18N not injected"
echo "$DESKTOP" | grep -q "tpl-terminal" || fail "terminal template missing"
echo "$DESKTOP" | grep -q "Web OS Login\|window.I18N" >/dev/null
echo "$DESKTOP" | grep -q "boot.js" || fail "boot.js not referenced"

# host CRUD round-trip
curl -sf -b "$COOKIE" -X POST "$BASE/api/hosts" \
  -H 'Content-Type: application/json' \
  -d '{"name":"smoke-test","host":"127.0.0.1","port":22,"username":"root","auth_method":"password","password":"x"}' \
  >/dev/null || fail "host create failed"
HOST_ID="$(curl -sf -b "$COOKIE" "$BASE/api/hosts" | node -e \
  'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{const h=JSON.parse(d).find(x=>x.name==="smoke-test");console.log(h?h.id:"")})')"
[ -n "$HOST_ID" ] || fail "host not listed"
curl -sf -b "$COOKIE" -X DELETE "$BASE/api/hosts/$HOST_ID" >/dev/null \
  || fail "host delete failed"

# attention rule test endpoint
curl -sf -b "$COOKIE" -X POST "$BASE/api/attention/rules/test" \
  -H 'Content-Type: application/json' \
  -d '{"rule":{"name":"t","match_mode":"all","patterns":[{"type":"contains_any","values":["[sudo] password for"]},{"type":"line_end_no_newline"}],"silence_ms":400,"confidence":"high","priority":100,"window_ms":30000},"sample":"[sudo] password for nil: "}' \
  | grep -q '"matched":true' || fail "attention rule test did not match"

# preview: unsupported type 415 (login-gated)
CODE=$(curl -s -o /dev/null -w '%{http_code}' -b "$COOKIE" \
  "$BASE/api/preview?source=local&path=does-not-exist.xyz")
[ "$CODE" = "404" ] || [ "$CODE" = "415" ] || fail "preview endpoint ($CODE)"

# unauthenticated API access is rejected
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/hosts")
[ "$CODE" = "401" ] || [ "$CODE" = "302" ] || fail "auth gate broken ($CODE)"

kill "$SERVER_PID" 2>/dev/null
echo "[smoke] ALL CHECKS PASSED"
