#!/usr/bin/env bash
# Install or update AI-DLC Studio into the LOCAL running KiroCrew gateway, grant narrow per-app trust,
# enable it, and print hook health. Idempotent: re-run after every backend change.
#
#   scripts/dev-install.sh            # build UI, install-or-update, trust, enable, health
#   scripts/dev-install.sh --no-build # skip the Vite build
#   scripts/dev-install.sh --dev      # also turn on dev mode (ui/ served no-store + live reload)
#
# Backend hook modules are only re-imported on enable, so this always disables before updating.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
APP="aidlc-studio"
KCAPI="$ROOT/scripts/kcapi.sh"
NODE_BIN="${NODE_BIN:-$(dirname "$(command -v node)")}"
export PATH="$NODE_BIN:$PATH"
PY3=/usr/bin/python3
BUILD=1; DEV=0
for a in "$@"; do
  case "$a" in
    --no-build) BUILD=0 ;;
    --dev) DEV=1 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
  esac
done

if [ "$BUILD" = 1 ] && [ -f "$ROOT/ui/package.json" ]; then
  echo "== building UI"
  (cd "$ROOT/ui" && "$NODE_BIN/npm" run build --silent)
  "$NODE_BIN/node" --check "$ROOT/ui/dist/index.mjs"
fi

echo "== install or update"
installed=$("$KCAPI" GET "/api/apps/${APP}" | $PY3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("no"); raise SystemExit
print("yes" if isinstance(d, dict) and d.get("name") else "no")' 2>/dev/null || echo no)
if [ "$installed" = "yes" ]; then
  "$KCAPI" POST "/api/apps/${APP}/disable" '{}' >/dev/null || true
  "$KCAPI" POST "/api/apps/${APP}/update" "{\"source\":\"${ROOT}\"}"
else
  "$KCAPI" POST "/api/apps/install" "{\"source\":\"${ROOT}\"}"
fi

echo "== trust (narrow, per-app)"
"$KCAPI" POST "/api/security/trusted-apps/${APP}" '{}' || true

echo "== enable"
"$KCAPI" POST "/api/apps/${APP}/enable" '{}'

if [ "$DEV" = 1 ]; then
  echo "== dev mode"
  "$KCAPI" POST "/api/apps/${APP}/dev" '{"enabled":true}'
fi

echo "== hook health"
"$KCAPI" GET "/api/apps" | APP="$APP" $PY3 -c '
import json, os, sys
apps = json.load(sys.stdin)
apps = apps if isinstance(apps, list) else apps.get("apps", [])
want = os.environ["APP"]
for a in apps:
    if a.get("name") == want:
        print(json.dumps({k: a.get(k) for k in ("name", "version", "enabled", "hooks", "backend_status")}, indent=1))
'
echo "== app health"
"$KCAPI" GET "/api/apps/${APP}/health"
