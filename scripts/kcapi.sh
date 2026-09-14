#!/usr/bin/env bash
# Call the LOCAL KiroCrew gateway API as the dashboard owner.
#
#   scripts/kcapi.sh GET  /api/apps
#   scripts/kcapi.sh POST /api/apps/aidlc-studio/repos '{"path":"/abs/repo"}'
#   scripts/kcapi.sh GET  /api/apps/aidlc-studio/health
#
# Auth (docs/research/01-kirocrew-app-runtime.md §7.3): `kirocrew token`, run with the gateway's OWN
# interpreter, mints a 5-minute link token signed with ~/.kiro/crew/token_signing.key; the first request
# carrying `?token=` exchanges it for the HttpOnly session cookie, which is cached in a jar and reused.
# The gateway has no `Authorization: Bearer` support — cookies are the contract.
#
# A standalone script rather than a sourced helper on purpose: the interactive shell here already has a
# `kc` alias, and `export -f` is bash-only, so sourcing was fragile.
set -euo pipefail

CURL=/usr/bin/curl
GREP=/usr/bin/grep
HEAD=/usr/bin/head
PY3=/usr/bin/python3

KC_PORT="${KC_PORT:-5476}"
KC_BASE="${KC_BASE:-http://127.0.0.1:${KC_PORT}}"
KC_JAR="${KC_JAR:-/tmp/kc-cookies-${KC_PORT}.jar}"
KC_PY="${KC_PY:-/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12}"

usage() { echo "usage: $(basename "$0") <METHOD> <PATH> [JSON_BODY]" >&2; exit 2; }
[ $# -ge 2 ] || usage
METHOD="$1"; PATH_="$2"; BODY="${3:-}"

session_ok() { $CURL -s -f -b "$KC_JAR" "${KC_BASE}/api/auth/me" >/dev/null 2>&1; }

mint() {
  local url token
  url=$("$KC_PY" -m kiro_crew token 2>/dev/null | $GREP -o 'token=.*' | $HEAD -1) || true
  token="${url#token=}"
  if [ -z "$token" ]; then
    echo "kcapi: could not mint a token — is the gateway running on ${KC_PORT}?" >&2
    exit 1
  fi
  $CURL -s -o /dev/null -c "$KC_JAR" "${KC_BASE}/?token=${token}"
  session_ok || { echo "kcapi: cookie exchange failed" >&2; exit 1; }
}

[ -s "$KC_JAR" ] && session_ok || mint

if [ -n "$BODY" ]; then
  $CURL -s -b "$KC_JAR" -c "$KC_JAR" -X "$METHOD" -H 'Content-Type: application/json' -d "$BODY" "${KC_BASE}${PATH_}"
else
  $CURL -s -b "$KC_JAR" -c "$KC_JAR" -X "$METHOD" "${KC_BASE}${PATH_}"
fi
echo
