"""Policy the code must satisfy by *shape*, not by behaviour (§4.3): could the code do the forbidden thing at all?

A shell string is a repository-path injection; a third ``subprocess`` module is a second place the env scrub, argv
allowlist, timeout and output cap must be remembered; a git write verb literal is one edit from Studio committing in a
user's repository; ``AIDLC_SKIP_*`` outside the two stripping modules disables AI-DLC's guardrails; a call into the
host's private dispatch functions collapses the two-phase human lane (A03, C24). Checks are AST-based where grep would
be blind or noisy, and only executable references count (a docstring naming a forbidden call is documentation).
``ui/src`` is scanned for raw HTML too: artifact bodies are someone else's text rendered in the dashboard's origin.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
BACKEND = APP_ROOT / "backend"
UI_SRC = APP_ROOT / "ui" / "src"
I18N = APP_ROOT / "ui" / "src" / "i18n"

# the only modules that may spawn a child: engine.py owns bun (the preflight's ``bun --version`` too, C35),
# git_observer.py owns git
SPAWN_MODULES = frozenset({"engine.py", "git_observer.py"})
# constants.py lists AIDLC_SKIP_* as the set that must be absent; security.py asserts their absence before every exec
ENV_POLICY_MODULES = frozenset({"constants.py", "security.py"})
# the host's chat internals: none may appear as a call, an attribute read or a getattr string anywhere in the backend
HOST_DISPATCH_NAMES = frozenset({
    "_run_chat", "spawn_guarded_turn", "run_background_turn", "enqueue_or_run_prompt", "get_or_create_slot",
    "queue_append", "stop_turn", "notify_user_input", "broadcast_ws", "push_refresh", "push_slots_update",
    "finish_turn_task", "_start_next_queued_turn", "drain_pending_context", "link_slack", "set_slack_link"})
# spelled again on purpose (not imported), so a member quietly deleted from constants.GIT_WRITE_VERBS still fails here
GIT_WRITE_VERBS = frozenset(
    "add commit push pull merge rebase checkout switch reset restore stash tag branch cherry-pick revert am apply "
    "clean rm mv fetch remote submodule worktree gc prune reflog update-ref symbolic-ref filter-branch".split())
# verbs with no second meaning here; ordinary words (``add`` is an engine kwarg, ``reset`` an SSE frame, ``merge`` an
# ownership class) are left to the argv-head rule, which is where they would actually be dangerous
UNAMBIGUOUS_GIT_WRITE_VERBS = GIT_WRITE_VERBS - {"add", "tag", "am", "rm", "mv", "reset", "branch", "merge", "apply",
                                                 "remote", "clean", "switch"}
DENY_TABLE_MODULES = ("constants.py", "security.py")


def backend_sources() -> list[Path]:
    return sorted(p for p in BACKEND.rglob("*.py") if "__pycache__" not in p.parts)


def parsed() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text("utf-8"), filename=str(p))) for p in backend_sources()]


def numbered_lines(path: Path, *, skip_comments: bool) -> list[tuple[int, str]]:
    return [(n, line) for n, line in enumerate(path.read_text("utf-8").splitlines(), start=1)
            if not (skip_comments and line.lstrip().startswith("#"))]


def _code_strings(tree: ast.Module) -> list[tuple[int, str]]:
    """Every string constant that is not a docstring: quoting a forbidden name to explain a rule must not fail."""
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)}
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings]


def _string_sequences(tree: ast.Module) -> list[tuple[int, list[str]]]:
    """List/tuple/set literals reduced to their string elements, with the line number."""
    sequences = ((node.lineno, [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)])
                 for node in ast.walk(tree) if isinstance(node, (ast.List, ast.Tuple, ast.Set)))
    return [(lineno, strings) for lineno, strings in sequences if strings]


# --- no shell, anywhere ---
def test_no_module_asks_for_a_shell():
    """``shell=True`` turns a repository path into shell source. There is no acceptable use of it."""
    offences = [f"{path.name}:{n}" for path in backend_sources()
                for n, line in numbered_lines(path, skip_comments=True) if "shell=True" in line]
    assert offences == [], offences


def test_every_shell_keyword_is_the_literal_false():
    """A computed ``shell=`` is the same hole with an extra step, so the value must be literal False."""
    offences = [f"{path.name}:{node.lineno}" for path, tree in parsed() for node in ast.walk(tree)
                if isinstance(node, ast.Call) for keyword in node.keywords
                if keyword.arg == "shell" and not (isinstance(keyword.value, ast.Constant)
                                                   and keyword.value.value is False)]
    assert offences == [], offences


@pytest.mark.parametrize("forbidden", ["os.system", "os.popen", "os.execv", "os.spawnl", "os.spawnv"])
def test_no_module_shells_out_through_os(forbidden):
    for path in backend_sources():
        lines = [f"{path.name}:{n}" for n, line in numbered_lines(path, skip_comments=True) if forbidden in line]
        assert lines == [], lines


# --- one door per external tool ---
def test_only_the_two_tool_modules_import_subprocess():
    importers = {path.name for path, tree in parsed() for node in ast.walk(tree)
                 if (isinstance(node, ast.Import) and any(a.name.split(".")[0] == "subprocess" for a in node.names))
                 or (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "subprocess")}
    assert importers == SPAWN_MODULES, sorted(importers)


def test_only_the_two_tool_modules_spawn_a_child():
    """Catches the spawn itself, not the import: ``os.fork``/``multiprocessing`` would slip an import test."""
    spawn_attrs = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
    spawners = {"fork", "os.fork", "os.forkpty", "posix_spawn", "os.posix_spawn", "create_subprocess_exec",
                "create_subprocess_shell", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}
    offences = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func, name = node.func, None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                name = f"{func.value.id}.{func.attr}"
            elif isinstance(func, ast.Name):
                name = func.id
            if name is None:
                continue
            spawning = ((name.startswith("subprocess.") and name.split(".", 1)[1] in spawn_attrs) or name in spawners
                        or name.startswith("multiprocessing."))
            if spawning and path.name not in SPAWN_MODULES:
                offences.append(f"{path.name}:{node.lineno}: {name}")
    assert offences == [], offences


def test_the_spawn_scan_would_catch_a_real_violation():
    """Not vacuous: the same visitor over a synthetic offence must find it."""
    tree = ast.parse('import subprocess\nsubprocess.run(["git", "commit"])\n')
    found = [node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"]
    assert found == ["run"]


# --- git stays read-only ---
def test_no_unambiguous_git_write_verb_appears_as_a_string():
    """``push``/``commit``/``checkout`` have no other meaning here, so any literal is a finding."""
    offences = [f"{path.name}:{lineno}: {value!r}" for path, tree in parsed() if path.name not in DENY_TABLE_MODULES
                for lineno, value in _code_strings(tree) if value in UNAMBIGUOUS_GIT_WRITE_VERBS]
    assert offences == [], offences


def test_no_string_sequence_starts_with_a_git_write_verb():
    """The argv shape: ``["commit", "-m", …]`` is a write even when the verb is an ordinary word."""
    offences = [f"{path.name}:{lineno}: {strings[:4]}" for path, tree in parsed() if path.name not in DENY_TABLE_MODULES
                for lineno, strings in _string_sequences(tree) if strings and strings[0] in GIT_WRITE_VERBS]
    assert offences == [], offences


def test_the_git_scan_would_catch_a_real_violation():
    heads = [s[0] for _lineno, s in _string_sequences(ast.parse('argv = ["commit", "-m", "wip"]\n')) if s]
    assert heads == ["commit"] and heads[0] in GIT_WRITE_VERBS


def test_the_production_write_verb_set_covers_this_files_copy(studio):
    """Production may be wider (``clone``, ``init``, …) but never narrower, or the argv check lets a write verb by."""
    assert GIT_WRITE_VERBS <= set(studio.constants.GIT_WRITE_VERBS)


# --- AI-DLC's guardrails are never disabled ---
def test_aidlc_skip_variables_appear_only_where_they_are_stripped():
    """``AIDLC_SKIP_*`` disables AI-DLC's own guards; a third module naming one would be Studio turning one off."""
    offences = [f"{path.name}:{n}: {line.strip()}" for path in backend_sources() if path.name not in ENV_POLICY_MODULES
                for n, line in numbered_lines(path, skip_comments=False) if "AIDLC_SKIP_" in line]
    assert offences == [], offences


def test_the_forbidden_env_names_are_actually_asserted_absent(studio):
    """The exemption above is only safe because those two modules use the names to *refuse* them."""
    assert {"AIDLC_SKIP_HUMAN_PRESENCE_GUARD", "AIDLC_SKIP_ARTIFACT_GUARD"} <= set(studio.constants.ENV_MUST_BE_ABSENT)
    env = studio.security.build_subprocess_env()
    assert not [k for k in env if k.startswith("AIDLC_") or k.startswith("CLAUDE_")]


# --- the host bridge stays read-mostly ---
def test_no_module_references_a_host_dispatch_function():
    """The human lane is two-phase (the backend records, the browser sends and reports): no fourth party in it."""
    offences = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in HOST_DISPATCH_NAMES:
                offences.append(f"{path.name}:{node.lineno}: name {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in HOST_DISPATCH_NAMES:
                offences.append(f"{path.name}:{node.lineno}: attribute .{node.attr}")
        # a name assembled as a string and fetched with getattr would defeat the AST walk above
        offences += [f"{path.name}:{lineno}: literal {value!r}" for lineno, value in _code_strings(tree)
                     if value in HOST_DISPATCH_NAMES]
    assert offences == [], offences


def test_no_module_appends_a_row_to_a_host_slot():
    """``slot.append("user", …)`` would forge the transcript the reconciler later reads as delivery evidence."""
    offences = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append":
                target = getattr(node.func.value, "id", None) or getattr(node.func.value, "attr", None) or ""
                if "slot" in str(target).lower():
                    offences.append(f"{path.name}:{node.lineno}: {target}.append(...)")
    assert offences == [], offences


def test_the_bridge_never_imports_a_private_host_module():
    """Host symbols resolve by name at call time; a top-level private import would fail to load on a renamed host."""
    offences = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for module in [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]:
                if module.startswith("kiro_crew.") and module.rsplit(".", 1)[-1].startswith("_"):
                    offences.append(f"{path.name}:{node.lineno}: {module}")
                if module.startswith("kiro_crew.dashboard.chat"):
                    offences.append(f"{path.name}:{node.lineno}: {module}")
    assert offences == [], offences


# --- imports inside the package stay relative (the loader has no parent package on 0.3.0) ---
def test_every_intra_package_import_is_relative():
    """The app is loaded by file path; ``backend/routes.py`` and ``hooks.py`` are pinned by the next test instead."""
    roots = {"backend", "studio", "_kirocrew_app_aidlc-studio"}
    offences = []
    for path, tree in parsed():
        if path.parent == BACKEND:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] in roots:
                offences.append(f"{path.name}:{node.lineno}: from {node.module or ''}")
            if isinstance(node, ast.Import):
                offences += [f"{path.name}:{node.lineno}: import {alias.name}" for alias in node.names
                             if alias.name.split(".")[0] in roots]
    assert offences == [], offences


def test_the_entry_points_import_studio_only_relatively():
    """The 0.8 loader registers the parent packages, so ``from . import studio`` is the one import that resolves."""
    for name in ("routes.py", "hooks.py"):
        tree = ast.parse((BACKEND / name).read_text("utf-8"))
        relative = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level > 0]
        assert [(n.level, n.module, [a.name for a in n.names]) for n in relative] == [(1, None, ["studio"])], name


# --- the UI never renders someone else's file as HTML ---
def test_the_ui_never_sets_inner_html():
    """Artifact bodies and audit rows are third-party text rendered inside the dashboard's origin."""
    if not UI_SRC.is_dir():
        pytest.skip("ui/src is not present in this checkout")
    pattern = re.compile(r"dangerouslySetInnerHTML|innerHTML\s*=")
    offences = [f"{path.relative_to(APP_ROOT)}:{n}: {line.strip()}" for path in sorted(UI_SRC.rglob("*"))
                if path.suffix in (".ts", ".tsx", ".js", ".jsx") and path.is_file()
                for n, line in numbered_lines(path, skip_comments=False) if pattern.search(line)]
    assert offences == [], offences


# --- every i18n key the backend emits resolves to real text ---
def _catalog(locale: str) -> dict[str, str]:
    path = I18N / f"{locale}.json"
    if not path.is_file():
        pytest.skip(f"{path.name} is not built in this checkout")
    return json.loads(path.read_text("utf-8"))


def _consequence_slots(node: ast.AST):
    """Every expression assigned to a consequence-variant slot: keyword argument or dict entry."""
    if isinstance(node, ast.keyword) and node.arg in ("consequence", "consequence_variant"):
        yield node.value
    if isinstance(node, ast.Dict):
        yield from (value for key, value in zip(node.keys, node.values)
                    if isinstance(key, ast.Constant) and key.value == "consequence_variant")


def _value_literals(node: ast.AST):
    """String constants this expression can evaluate to (``a if t == "run" else b`` never yields ``"run"``)."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            yield node
    elif isinstance(node, ast.IfExp):
        yield from _value_literals(node.body)
        yield from _value_literals(node.orelse)
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from _value_literals(value)


def test_every_card_type_has_a_headline_and_its_consequences(studio):
    """A card with no copy renders a generic fallback (it shipped once, for every type); plain ``pytest`` fails too."""
    constants = studio.constants
    for locale in ("en-US", "zh-CN"):
        catalog = _catalog(locale)
        missing = [f"action.{t}.headline" for t in constants.ACTION_TYPE if f"action.{t}.headline" not in catalog]
        missing += [f"action.{t}.consequence.{v}" for t, variants in constants.CARD_CONSEQUENCE_VARIANTS.items()
                    for v in variants if f"action.{t}.consequence.{v}" not in catalog]
        assert missing == [], f"{locale}: {missing}"


def test_the_declared_consequence_variants_cover_every_emission_site(studio):
    """The declared variants must cover the seed table and every explicit ``consequence=`` override in the backend."""
    declared = studio.constants.CARD_CONSEQUENCE_VARIANTS
    for action_type, variant in studio.projection.SEED_CONSEQUENCE.items():
        if variant is not None:
            assert variant in declared.get(action_type, ()), f"seed {action_type}={variant} undeclared"
    every_variant = {v for variants in declared.values() for v in variants}
    literals = [f"{path.name}:{leaf.lineno}:{leaf.value}" for path, tree in parsed() for node in ast.walk(tree)
                for value in _consequence_slots(node) for leaf in _value_literals(value)]
    # ``__default__`` is the sentinel meaning "use the seed table", not a variant name
    undeclared = [entry for entry in literals if entry.rsplit(":", 1)[1] not in every_variant | {"__default__"}]
    assert undeclared == [], undeclared
    assert literals, "the scan found no consequence literals at all — it has stopped working"
