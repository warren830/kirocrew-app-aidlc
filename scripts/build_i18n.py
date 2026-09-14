#!/usr/bin/env python3
"""Merge the per-area translation parts into the two shipped catalogs, and check them.

Why parts: the catalog is edited by whoever writes a page, and a single 700-key JSON file edited by
several people at once is a merge conflict generator. Each area owns
``ui/src/i18n/parts/<area>.<locale>.json`` and this script merges them into
``ui/src/i18n/en-US.json`` / ``zh-CN.json``, which are what the bundle imports.

Checks that fail the build (they are the i18n gate):
  * a key defined in two parts (ambiguous ownership),
  * a key present in one locale and missing in the other,
  * an empty value,
  * a placeholder set that differs between locales (``{name}`` dropped in translation),
  * a missing ``errors.<code>`` for any code in ``backend/studio/errors.py``,
  * a missing ``enum.<group>.<value>`` for any canonical enum value,
  * a missing ``action.<type>.headline`` or ``action.<type>.consequence.<variant>`` for any card the
    backend can build (``ACTION_TYPE`` and ``CARD_CONSEQUENCE_VARIANTS``).

Usage: ``python3 scripts/build_i18n.py [--check]``. ``--check`` writes nothing and exits non-zero on
any problem, which is what CI runs.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "ui" / "src" / "i18n"
PARTS = I18N / "parts"
LOCALES = ("en-US", "zh-CN")
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")

ENUM_GROUPS = {
    "actionStatus": "ACTION_STATUS",
    "intentState": "INTENT_STATE",
    "actionType": "ACTION_TYPE",
    "severity": "SEVERITY",
    "findingSeverity": "FINDING_SEVERITY",
    "source": "SOURCE",
    "stageState": "STAGE_STATE",
    "installStatus": "INSTALL_STATUS",
    "availability": "AVAILABILITY",
    "ownership": "OWNERSHIP",
    "phase": "PHASES",
}


def load_backend(name: str):
    ns = "_aidlc_studio_backend"
    if ns not in sys.modules:
        spec = importlib.machinery.ModuleSpec(ns, None, is_package=True)
        mod = importlib.util.module_from_spec(spec)
        mod.__path__ = [str(ROOT / "backend")]  # type: ignore[attr-defined]
        sys.modules[ns] = mod
    return importlib.import_module(f"{ns}.studio.{name}")


def merge(locale: str) -> tuple[dict[str, str], list[str]]:
    merged: dict[str, str] = {}
    owner: dict[str, str] = {}
    problems: list[str] = []
    for path in sorted(PARTS.glob(f"*.{locale}.json")):
        area = path.name.split(".")[0]
        try:
            data = json.loads(path.read_text("utf-8"))
        except ValueError as exc:
            problems.append(f"{path.name}: invalid JSON ({exc})")
            continue
        if not isinstance(data, dict):
            problems.append(f"{path.name}: top level must be an object")
            continue
        for key, value in data.items():
            if key in merged:
                problems.append(f"key {key!r} defined in both {owner[key]} and {area} ({locale})")
                continue
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{area} ({locale}): key {key!r} has an empty or non-string value")
                continue
            merged[key] = value
            owner[key] = area
    return merged, problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only; write nothing")
    args = ap.parse_args(argv)

    if not PARTS.is_dir():
        print(f"no parts directory at {PARTS}", file=sys.stderr)
        return 2

    catalogs: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    for locale in LOCALES:
        catalogs[locale], errs = merge(locale)
        problems.extend(errs)

    base, other = catalogs[LOCALES[0]], catalogs[LOCALES[1]]
    for key in sorted(set(base) - set(other)):
        problems.append(f"key {key!r} missing from {LOCALES[1]}")
    for key in sorted(set(other) - set(base)):
        problems.append(f"key {key!r} missing from {LOCALES[0]}")
    for key in sorted(set(base) & set(other)):
        want = set(PLACEHOLDER_RE.findall(base[key]))
        got = set(PLACEHOLDER_RE.findall(other[key]))
        if want != got:
            problems.append(
                f"key {key!r}: placeholders differ ({sorted(want)} vs {sorted(got)})"
            )

    # Coverage of the backend's own tables: a code or enum value with no string renders as a raw key.
    errors_mod = load_backend("errors")
    constants = load_backend("constants")
    for code in errors_mod.ERROR_CODES:
        if f"errors.{code}" not in base:
            problems.append(f"missing errors.{code} (backend error code has no message)")
    for group, attr in ENUM_GROUPS.items():
        for value in getattr(constants, attr):
            if f"enum.{group}.{value}" not in base:
                problems.append(f"missing enum.{group}.{value}")

    # Card copy. A card whose headline key is absent renders a generic "not available in this build"
    # fallback -- readable, but it tells the reader nothing about their own work, so it is a defect and
    # not a graceful degradation. Same for the consequence sentence, which is the one line that says
    # what acting will DO.
    for action_type in constants.ACTION_TYPE:
        if f"action.{action_type}.headline" not in base:
            problems.append(f"missing action.{action_type}.headline (card type has no summary)")
    for action_type, variants in constants.CARD_CONSEQUENCE_VARIANTS.items():
        for variant in variants:
            key = f"action.{action_type}.consequence.{variant}"
            if key not in base:
                problems.append(f"missing {key} (card consequence has no text)")

    if problems:
        for problem in problems:
            print(f"i18n: {problem}", file=sys.stderr)
        print(f"i18n: {len(problems)} problem(s)", file=sys.stderr)
        return 1

    if not args.check:
        for locale, catalog in catalogs.items():
            path = I18N / f"{locale}.json"
            text = json.dumps(dict(sorted(catalog.items())), ensure_ascii=False, indent=2) + "\n"
            if not path.is_file() or path.read_text("utf-8") != text:
                path.write_text(text, encoding="utf-8")
        print(f"i18n: {len(base)} keys per locale, {len(LOCALES)} locales")
    else:
        print(f"i18n: ok, {len(base)} keys per locale")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
