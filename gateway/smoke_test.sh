#!/usr/bin/env bash
# Non-destructive checks of a running gateway; creates nothing.
#   ./smoke_test.sh [http://host:8080]
# Optional environment:
#   LIBRETHINKERAGENT_ADMIN_TOKEN  admin API checks (read from ./.env when unset)
#   DEVICE_TOKEN                   also lists models and sends one tiny chat request
set -u
cd "$(dirname "$0")"
base=${1:-http://127.0.0.1:8080}; base=${base%/}
admin=${LIBRETHINKERAGENT_ADMIN_TOKEN:-}
if [ -z "$admin" ] && [ -f .env ]; then
  admin=$(grep -E '^LIBRETHINKERAGENT_ADMIN_TOKEN=' .env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//')
fi
fail=0
check() {  # name expected actual
  if [ "$2" = "$3" ]; then echo "PASS  $1"; else echo "FAIL  $1 (expected $2, got $3)"; fail=1; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' -m 30 "$@"; }

check "health"                          200 "$(code "$base/healthz")"
check "dashboard page"                  200 "$(code "$base/")"
check "requests page"                   200 "$(code "$base/requests")"
check "revoked page"                    200 "$(code "$base/revoked")"
check "admin API refuses no token"      401 "$(code "$base/admin/devices")"
check "model list refuses no token"     401 "$(code "$base/v1/models")"
check "extension download"              200 "$(code "$base/setup/LibreThinkerAgent.oxt")"
size=$(curl -s -m 60 "$base/setup/LibreThinkerAgent.oxt" | wc -c)
[ "$size" -gt 1000000 ] && echo "PASS  extension size (${size} bytes)" || { echo "FAIL  extension too small (${size} bytes)"; fail=1; }
curl -s -D - -o /dev/null -m 10 "$base/healthz" | grep -qi '^x-content-type-options: nosniff' \
  && echo "PASS  security headers" || { echo "FAIL  security headers missing"; fail=1; }

if [ -n "$admin" ]; then
  check "admin API with token"          200 "$(code -H "X-Admin-Token: $admin" "$base/admin/devices")"
  check "admin stats"                   200 "$(code -H "X-Admin-Token: $admin" "$base/admin/stats")"
else
  echo "SKIP  admin API checks (no LIBRETHINKERAGENT_ADMIN_TOKEN)"
fi

if [ -n "${DEVICE_TOKEN:-}" ]; then
  check "model list with device token"  200 "$(code -H "Authorization: Bearer $DEVICE_TOKEN" "$base/v1/models")"
  model=$(curl -s -m 20 -H "Authorization: Bearer $DEVICE_TOKEN" "$base/v1/models" | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null)
  check "chat request"                  200 "$(code -H "Authorization: Bearer $DEVICE_TOKEN" -H 'Content-Type: application/json' \
      -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word OK.\"}],\"max_tokens\":16}" \
      "$base/v1/chat/completions")"
else
  echo "SKIP  device checks (set DEVICE_TOKEN to test a real chat request)"
fi
[ "$fail" -eq 0 ] && echo "All checks passed." || echo "Some checks FAILED."
exit "$fail"
