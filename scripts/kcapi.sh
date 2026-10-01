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
KC_PORT="${KC_PORT:-5476}"
KC_BASE="${KC_BASE:-http://127.0.0.1:${KC_PORT}}"
KC_JAR="${KC_JAR:-/tmp/kc-cookies-${KC_PORT}.jar}"
# The gateway's own Python mints the token. Same default as dev-install.sh, which passes its choice on.
BUNDLED_PY=/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12
if [ -z "${KC_PY:-}" ]; then
  if [ -x "$BUNDLED_PY" ]; then KC_PY="$BUNDLED_PY"; else KC_PY=python3; fi
fi

usage() { echo "usage: $(basename "$0") <METHOD> <PATH> [JSON_BODY]" >&2; exit 2; }
[ $# -ge 2 ] || usage
METHOD="$1"; PATH_="$2"; BODY="${3:-}"

session_ok() { $CURL -s -f -b "$KC_JAR" "${KC_BASE}/api/auth/me" >/dev/null 2>&1; }

mint() {
  local url token
  if ! command -v "$KC_PY" >/dev/null 2>&1; then
    echo "kcapi: gateway Python not found: ${KC_PY}; set KC_PY to the Python that runs KiroCrew" >&2
    exit 1
  fi
  url=$("$KC_PY" -m kiro_crew token 2>/dev/null | $GREP -o 'token=.*' | $HEAD -1) || true
  token="${url#token=}"
  if [ -z "$token" ]; then
    echo "kcapi: could not mint a token with ${KC_PY} — is the gateway running on ${KC_PORT}, and is KC_PY its own Python?" >&2
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
