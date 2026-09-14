#!/usr/bin/env bash
# One command that says whether AI-DLC Studio is releasable from this working tree.
#
#   scripts/check.sh              # everything
#   scripts/check.sh backend      # python only
#   scripts/check.sh frontend     # ui only
#   scripts/check.sh --fast       # skip the ui build and the bundle-content greps
#
# Every step is a release gate from the PRD's quality-gates list, in the order that fails cheapest
# first. The generated-source step is the interesting one: it regenerates the UI's copies of the wire
# text, error codes and enums and fails if the tree changed, which is how a backend change that would
# have silently desynchronised the two languages becomes a build failure instead of a wrong decision
# sent to AI-DLC.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

PY="${PY:-/Users/ychchen/warren_ws/kirocrew/.venv/bin/python}"
BUNDLE_PY="${BUNDLE_PY:-/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12}"
NODE_BIN="${NODE_BIN:-$(dirname "$(command -v node)")}"
export PATH="$NODE_BIN:$PATH"

SCOPE="all"
FAST=0
for arg in "$@"; do
  case "$arg" in
    backend|frontend|all) SCOPE="$arg" ;;
    --fast) FAST=1 ;;
    *) echo "usage: $0 [backend|frontend|all] [--fast]" >&2; exit 2 ;;
  esac
done

FAILED=()
step() {
  local name="$1"; shift
  printf '\n\033[1m== %s\033[0m\n' "$name"
  if "$@"; then
    printf '\033[32mok\033[0m  %s\n' "$name"
  else
    printf '\033[31mFAILED\033[0m  %s\n' "$name"
    FAILED+=("$name")
  fi
}

manifest_valid() {
  "$PY" - <<'PYEOF'
import json, sys
from pathlib import Path
from kiro_crew.apps.manifest import AppManifest

root = Path.cwd()
manifest = AppManifest.from_dict(json.loads((root / "app.json").read_text()))
errors = manifest.validate(app_root=root)
if errors:
    print("manifest errors:", errors, file=sys.stderr)
    sys.exit(1)

payload = json.loads((root / "payload" / "manifest.json").read_text())
extra = manifest.extra.get("extra", {}).get("bundledAidlc", {})
if extra.get("engineVersion") != payload["engineVersion"]:
    print(f"app.json bundledAidlc.engineVersion {extra.get('engineVersion')!r} != payload {payload['engineVersion']!r}",
          file=sys.stderr)
    sys.exit(1)

for rel in ("assets/icon-512.png", "assets/hero-light.svg", "assets/hero-dark.svg",
            "ui/icon.svg", "ui/dist/index.mjs", "agents/advisor.json", "LICENSE",
            "payload/AIDLC-LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
    if not (root / rel).is_file():
        print(f"missing release asset: {rel}", file=sys.stderr)
        sys.exit(1)

advisor = json.loads((root / "agents" / "advisor.json").read_text())
if advisor.get("tools") != ["thinking"]:
    print(f"advisor agent must have tools == ['thinking'], got {advisor.get('tools')!r}", file=sys.stderr)
    sys.exit(1)
print("manifest, payload version, release assets and advisor tool surface all agree")
PYEOF
}

payload_intact() {
  "$PY" - <<'PYEOF'
import hashlib, json, sys
from pathlib import Path

root = Path.cwd()
manifest = json.loads((root / "payload" / "manifest.json").read_text())
base = root / "payload" / "aidlc-kiro"
bad = []
digest = hashlib.sha256()
for entry in manifest["files"]:
    path = base / entry["path"]
    if not path.is_file():
        bad.append(f"missing {entry['path']}")
        continue
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != entry["sha256"]:
        bad.append(f"digest changed: {entry['path']}")
    digest.update(f"{entry['path']}\0{got}\n".encode())
on_disk = {str(p.relative_to(base).as_posix()) for p in base.rglob("*") if p.is_file()}
extra = on_disk - {e["path"] for e in manifest["files"]}
if extra:
    bad.append(f"{len(extra)} file(s) in the payload are not in the manifest: {sorted(extra)[:3]}")
if digest.hexdigest() != manifest["payloadDigest"]:
    bad.append("payloadDigest does not match the files")
if bad:
    print("\n".join(bad), file=sys.stderr)
    sys.exit(1)
print(f"payload intact: {len(manifest['files'])} files, AI-DLC {manifest['engineVersion']}")
PYEOF
}

generated_current() {
  local before after
  before=$(cd "$ROOT" && cat ui/src/lib/*.generated.ts 2>/dev/null | shasum | cut -d' ' -f1)
  "$PY" scripts/gen_ui_sources.py >/dev/null || return 1
  after=$(cd "$ROOT" && cat ui/src/lib/*.generated.ts | shasum | cut -d' ' -f1)
  if [ "$before" != "$after" ]; then
    echo "generated UI sources were stale — they have been regenerated; commit them" >&2
    return 1
  fi
  echo "wire text, error codes and enums are in sync with the backend"
}

bundle_clean() {
  # A literal colour in the bundle means a theme will break; raw HTML injection means an artifact could
  # execute. Both are cheap to grep for and expensive to find later.
  local bad=0
  if grep -nE '#[0-9a-fA-F]{6}\b' ui/dist/index.mjs >/dev/null 2>&1; then
    echo "bundle contains a literal hex colour:" >&2
    grep -noE '#[0-9a-fA-F]{6}\b' ui/dist/index.mjs | head -5 >&2
    bad=1
  fi
  if grep -n 'dangerouslySetInnerHTML' ui/dist/index.mjs >/dev/null 2>&1; then
    echo "bundle uses dangerouslySetInnerHTML" >&2
    bad=1
  fi
  [ "$bad" = 0 ] && echo "bundle carries no literal colour and no raw HTML injection"
  return $bad
}

if [ "$SCOPE" != "frontend" ]; then
  step "app manifest and release assets" manifest_valid
  step "bundled AI-DLC payload integrity" payload_intact
  step "backend tests" "$PY" -m pytest tests -q -o addopts=
  step "backend imports under the gateway's own interpreter" "$BUNDLE_PY" - <<'PYEOF'
import importlib, importlib.machinery, importlib.util, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
from pathlib import Path
ns = "_aidlc_studio_backend"
mod = importlib.util.module_from_spec(importlib.machinery.ModuleSpec(ns, None, is_package=True))
mod.__path__ = [str(Path.cwd() / "backend")]
sys.modules[ns] = mod
studio = importlib.import_module(f"{ns}.studio")
print("imports on the live gateway interpreter:", len(studio.__all__), "modules")
PYEOF
fi

if [ "$SCOPE" != "backend" ]; then
  step "generated UI sources are current" generated_current
  step "i18n catalogs" "$PY" scripts/build_i18n.py --check
  step "ui typecheck" env -C ui "$NODE_BIN/npx" tsc --noEmit -p tsconfig.json
  if [ -d ui/node_modules/vitest ]; then
    # --passWithNoTests: an area whose tests are not written yet must not read as a broken gate.
    step "ui tests" env -C ui "$NODE_BIN/npx" vitest run --silent --passWithNoTests
  fi
  if [ "$FAST" = 0 ]; then
    step "ui build" env -C ui "$NODE_BIN/npm" run build --silent
    step "bundle parses" "$NODE_BIN/node" --check ui/dist/index.mjs
    step "bundle content policy" bundle_clean
  fi
fi

printf '\n'
if [ ${#FAILED[@]} -eq 0 ]; then
  printf '\033[32mALL CHECKS PASSED\033[0m\n'
  exit 0
fi
printf '\033[31m%d CHECK(S) FAILED:\033[0m\n' "${#FAILED[@]}"
for name in "${FAILED[@]}"; do printf '  - %s\n' "$name"; done
exit 1
