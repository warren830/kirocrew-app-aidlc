#!/usr/bin/env bash
# Install this working tree into the local gateway under ANOTHER app name, next to the shipped Studio.
#
#   scripts/dev-slug.sh aidlc-studio-next              # build, stage, rename, install, trust, enable
#   scripts/dev-slug.sh aidlc-studio-next --no-build   # reuse ui/dist as it is
#   scripts/dev-slug.sh aidlc-studio-v2 --label "AI-DLC Studio v2"   # the name the sidebar shows
#
# The staged copy differs from this tree only where the app name is written down: app.json (name,
# display name, page route, API prefix, event names) and the advisor agent's declared name. Backend and
# UI derive the name at runtime. Keep write actions to test repositories: two Studios bound to the same
# intent would each create their own conversation for it.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
SLUG="${1:?usage: $0 <app-name> [--no-build]}"
shift
BUILD=1
LABEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --label) LABEL="${2:?--label needs a value}"; shift ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$SLUG" in
  *[!a-z0-9-]*|-*|*-) echo "dev-slug: app names are kebab-case ([a-z0-9-]): $SLUG" >&2; exit 2 ;;
esac
PY3="${KC_PY:-python3}"

if [ "$BUILD" = 1 ]; then
  NODE_BIN="${NODE_BIN:-$(dirname "$(command -v node)")}"
  (cd "$ROOT/ui" && PATH="$NODE_BIN:$PATH" "$NODE_BIN/npm" run build --silent)
  "$NODE_BIN/node" --check "$ROOT/ui/dist/index.mjs"
fi

STAGE="${TMPDIR:-/tmp}/aidlc-studio-stage/$SLUG"
rm -rf "$STAGE"
mkdir -p "$STAGE"
rsync -a --exclude .git --exclude ui/node_modules --exclude tests --exclude mockups \
  --exclude '__pycache__' "$ROOT/" "$STAGE/"

"$PY3" - "$STAGE" "$SLUG" "$LABEL" <<'PYEOF'
import json, sys
from pathlib import Path

stage, slug, label = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
manifest_path = stage / "app.json"
manifest = json.loads(manifest_path.read_text("utf-8"))
old = manifest["name"]
manifest["name"] = slug
manifest["displayName"] = label or f"{manifest['displayName']} ({slug})"
for page in manifest.get("ui", {}).get("pages", []):
    page["route"] = page["route"].replace(f"/apps/{old}", f"/apps/{slug}")
    page["label"] = label or f"{page['label']} ({slug})"
perms = manifest.get("permissions", {})
perms["api"] = [p.replace(f"/api/apps/{old}", f"/api/apps/{slug}") for p in perms.get("api", [])]
perms["events"] = [e.replace(f"{old}:", f"{slug}:", 1) for e in perms.get("events", [])]
manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

for rel in manifest.get("agents", []):
    agent_path = stage / rel
    agent = json.loads(agent_path.read_text("utf-8"))
    if agent.get("name", "").startswith(f"{old}-"):
        agent["name"] = slug + agent["name"][len(old):]
    agent_path.write_text(json.dumps(agent, indent=2, ensure_ascii=False) + "\n")
print(f"staged {old} as {slug} in {stage}")
PYEOF

"$STAGE/scripts/dev-install.sh" --no-build
