#!/usr/bin/env bash
# Rebuild payload/aidlc-kiro from an extracted upstream runtime/kiro plus Studio's local patches.
#
#   scripts/refresh_payload.sh apply <runtime/kiro>   # copy upstream in, then apply scripts/payload-patches/*.patch
#   scripts/refresh_payload.sh recut <runtime/kiro>   # rewrite each patch as `diff -u` of upstream vs payload
#
# `apply` dry-runs each patch with no fuzz before writing it, so a patch whose context upstream changed is
# rejected instead of landing somewhere else, and leaves no .rej or .orig behind. It applies every patch that
# fits and exits 1 naming the rest: make those changes by hand in the payload, then `recut` against the same
# upstream tree. Each patch names its own target in its `+++ b/` line.
# Regenerate payload/manifest.json afterwards (README, Development).
set -euo pipefail
cd "$(dirname "$0")/.."
PATCHES="$PWD/scripts/payload-patches"
DEST="$PWD/payload/aidlc-kiro"

usage() { echo "usage: $0 apply|recut <extracted runtime/kiro>" >&2; exit 2; }
[ $# -eq 2 ] || usage
MODE="$1"
UP="$(cd "$2" && pwd)"
[ -f "$UP/.kiro/tools/aidlc-version.ts" ] || { echo "$UP is not an AI-DLC runtime/kiro tree" >&2; exit 2; }

target_of() { sed -n 's|^+++ b/\([^[:space:]]*\).*|\1|p' "$1" | head -n 1; }

case "$MODE" in
  apply)
    rsync -a --delete "$UP/" "$DEST/"
    failed=()
    for patch_file in "$PATCHES"/*.patch; do
      if patch -d "$DEST" -p1 -N -t -F 0 -V none -s --dry-run < "$patch_file"; then
        patch -d "$DEST" -p1 -N -t -F 0 -V none -s < "$patch_file"
      else
        failed+=("$(basename "$patch_file")")
      fi
    done
    if [ ${#failed[@]} -gt 0 ]; then
      echo "REJECTED against $UP: ${failed[*]}" >&2
      echo "the other patches are applied; make those changes by hand in payload/aidlc-kiro, then run recut" >&2
      exit 1
    fi
    ;;
  recut)
    for patch_file in "$PATCHES"/*.patch; do
      rel="$(target_of "$patch_file")"
      [ -n "$rel" ] || { echo "$(basename "$patch_file") names no +++ b/ target" >&2; exit 1; }
      status=0
      diff -u --label "a/$rel" --label "b/$rel" "$UP/$rel" "$DEST/$rel" > "$patch_file" || status=$?
      case "$status" in
        1) ;;
        0) echo "$rel no longer differs from upstream: delete $(basename "$patch_file")" >&2; exit 1 ;;
        *) exit 1 ;;
      esac
    done
    ;;
  *) usage ;;
esac
echo "payload/aidlc-kiro: $MODE done against $UP"
