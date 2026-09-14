#!/usr/bin/env bash
# Capture the release screenshots from the RUNNING app in a real browser.
#
#   scripts/capture_screenshots.sh                       # every shot, current host theme
#   scripts/capture_screenshots.sh --action <action_id>   # select a specific queue item
#   scripts/capture_screenshots.sh --repo <repo_id> --intent <intent_key>
#   scripts/capture_screenshots.sh --mobile              # 390px list and detail
#
# Why a real browser rather than a component snapshot: the app renders inside the KiroCrew dashboard,
# against the host's own theme tokens and import map. A screenshot taken any other way cannot prove the
# thing the release checklist actually asks about — that it looks right *there*.
#
# Auth: the same 5-minute link token `scripts/kcapi.sh` uses. The browser exchanges it for the session
# cookie on the first navigation, exactly as a person's browser does.
set -uo pipefail
cd "$(dirname "$0")/.."

OUT="${OUT:-assets/screenshots}"
PLAYWRIGHT="${PLAYWRIGHT:-/Users/ychchen/.nvm/versions/node/v25.2.1/bin/playwright}"
BUNDLE_PY="${BUNDLE_PY:-/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12}"
BASE="${BASE:-http://localhost:5476}"
WAIT="${WAIT:-6000}"
DESKTOP="1600,1000"
MOBILE="390,844"

ACTION=""; REPO=""; INTENT=""; MOBILE_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --action) ACTION="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --intent) INTENT="$2"; shift 2 ;;
    --mobile) MOBILE_ONLY=1; shift ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

if [ ! -x "$PLAYWRIGHT" ]; then
  echo "playwright CLI not found at $PLAYWRIGHT — install it or set PLAYWRIGHT=" >&2
  exit 1
fi

TOKEN=$("$BUNDLE_PY" -m kiro_crew token 2>/dev/null | /usr/bin/grep -o 'token=.*' | /usr/bin/cut -d= -f2)
if [ -z "$TOKEN" ]; then
  echo "could not mint a dashboard token — is the gateway running?" >&2
  exit 1
fi

mkdir -p "$OUT"

# Fill in the newest action and the first repo/intent when the caller did not name them, so the shots
# show real data instead of empty states.
if [ -z "$ACTION" ]; then
  ACTION=$(scripts/kcapi.sh GET /api/apps/aidlc-studio/actions 2>/dev/null |
    /usr/bin/python3 -c 'import json,sys
try:
    rows = json.load(sys.stdin).get("actions") or []
except Exception:
    rows = []
print(rows[0]["action_id"] if rows else "")' 2>/dev/null)
fi
if [ -z "$REPO" ]; then
  read -r REPO INTENT <<<"$(scripts/kcapi.sh GET /api/apps/aidlc-studio/actions 2>/dev/null |
    /usr/bin/python3 -c 'import json,sys
try:
    rows = json.load(sys.stdin).get("actions") or []
except Exception:
    rows = []
if rows:
    print(rows[0]["repo"]["repo_id"], rows[0]["intent"]["intent_key"])' 2>/dev/null)"
fi

shot() { # shot <name> <query> <viewport>
  local name="$1" query="$2" viewport="$3"
  if "$PLAYWRIGHT" screenshot --viewport-size "$viewport" --wait-for-timeout "$WAIT" \
      "${BASE}/apps/aidlc-studio?token=${TOKEN}${query}" "${OUT}/${name}.png" >/dev/null 2>&1; then
    printf '  %-28s %s\n' "$name" "$(/usr/bin/wc -c <"${OUT}/${name}.png" | tr -d ' ') bytes"
  else
    printf '  %-28s FAILED\n' "$name"
  fi
}

scope=""
[ -n "$REPO" ] && scope="&repo=${REPO}"
[ -n "$INTENT" ] && scope="${scope}&intent=${INTENT}"

if [ "$MOBILE_ONLY" = 0 ]; then
  echo "desktop ($DESKTOP):"
  shot action-center-empty "&view=actions" "$DESKTOP"
  [ -n "$ACTION" ] && shot action-center-gate "&view=actions&action=${ACTION}" "$DESKTOP"
  [ -n "$ACTION" ] && shot action-artifacts "&view=actions&action=${ACTION}&tab=artifacts" "$DESKTOP"
  shot repos "&view=repos" "$DESKTOP"
  shot intents "&view=intents${scope}" "$DESKTOP"
  shot workflow-map "&view=map${scope}" "$DESKTOP"
  shot activity "&view=activity${scope}" "$DESKTOP"
  shot settings "&view=settings" "$DESKTOP"
  shot new-intent "&view=new-intent${scope}" "$DESKTOP"
fi

echo "mobile ($MOBILE):"
shot mobile-queue "&view=actions" "$MOBILE"
[ -n "$ACTION" ] && shot mobile-detail "&view=actions&action=${ACTION}" "$MOBILE"

cat <<EOF

Captured into ${OUT}. For the release checklist, run this twice — once with the dashboard in dark mode and
once in light — and once per language (Settings → language, or Studio's own locale override). The host
owns the theme, so Studio cannot switch it for you.
EOF
