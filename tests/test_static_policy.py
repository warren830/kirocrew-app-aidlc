"""Policy the code must satisfy by *shape*, not by behaviour (§4.3).

Every other test file asks "given this input, what does the code do?". This one asks "could the code
do the forbidden thing at all?" — because the failures it guards against are the ones no unit test
reaches:

* a shell string instead of an argv list is a repository-path injection, and the repository path is
  attacker-controlled the moment a user registers a directory someone else wrote;
* a ``subprocess`` call in a third module is a second place where the environment scrub, the argv
  allowlist, the timeout and the output cap have to be remembered — and they will not be;
* a git write verb as a literal is one edit away from Studio committing in a user's repository, which
  the product promises never to happen;
* ``AIDLC_SKIP_*`` outside the two modules that exist to *strip* those variables would be Studio
  disabling AI-DLC's own guardrails;
* a call to the host's private dispatch functions (``_run_chat``, ``spawn_guarded_turn``,
  ``slot.append``) collapses the two-phase human lane into a one-phase in-process send, which loses
  the durable ``Delivering`` record, the ``HUMAN_TURN`` the conductor needs, and the whole
  at-most-once guarantee (architecture A03, C24).

The checks are AST-based wherever a bare grep would be either blind (a name built by
``getattr(mod, "_run" + "_chat")``) or noisy (the word ``add`` is a legitimate engine kwarg). Prose is
exempted deliberately: a docstring that *names* the forbidden call in order to say "never call this" is
the documentation this file wants to exist, so only executable references count.

``ui/src`` is scanned for ``dangerouslySetInnerHTML`` from here too: artifact bodies and audit rows are
someone else's file contents rendered in the dashboard's own origin, so raw HTML injection would be a
stored-XSS hole with a session cookie behind it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
BACKEND = APP_ROOT / "backend"
UI_SRC = APP_ROOT / "ui" / "src"

#: The two modules allowed to spawn a child process — one per external tool. ``engine.py`` owns ``bun``
#: (including the ``bun --version`` probe the preflight uses, C35) and ``git_observer.py`` owns ``git``.
#: Nothing else, ever: the guards live at those two doors.
SPAWN_MODULES = frozenset({"engine.py", "git_observer.py"})

#: The modules that may name an ``AIDLC_SKIP_*`` variable: ``constants.py`` lists them as the set that
#: must be absent, ``security.py`` asserts their absence before every exec.
ENV_POLICY_MODULES = frozenset({"constants.py", "security.py"})

#: Attribute/function names from the host's chat internals. ``HostBridge`` is read-mostly by contract:
#: the UI sends through the public ``POST /api/chat`` and reports back, so none of these may appear as a
#: call, an attribute read, or a ``getattr`` string anywhere in the backend.
HOST_DISPATCH_NAMES = frozenset(
    {
        "_run_chat",
        "spawn_guarded_turn",
        "run_background_turn",
        "enqueue_or_run_prompt",
        "get_or_create_slot",
        "queue_append",
        "stop_turn",
        "notify_user_input",
        "broadcast_ws",
        "push_refresh",
        "push_slots_update",
        "finish_turn_task",
        "_start_next_queued_turn",
        "drain_pending_context",
        "link_slack",
        "set_slack_link",
    }
)

#: Every git verb that writes. Mirrors ``constants.GIT_WRITE_VERBS``; spelled again here on purpose, so
#: a member quietly deleted from the production set still fails this file.
GIT_WRITE_VERBS = frozenset(
    "add commit push pull merge rebase checkout switch reset restore stash tag branch cherry-pick "
    "revert am apply clean rm mv fetch remote submodule worktree gc prune reflog update-ref "
    "symbolic-ref filter-branch".split()
)

#: Write verbs with no second meaning in this codebase, so their bare presence as a string is a finding.
#: ``add``/``tag``/``am``/``rm``/``mv``/``reset``/``branch`` are excluded because they are ordinary words
#: (``add`` is an engine kwarg, ``reset`` an SSE control frame); ``merge`` is excluded because it is the
#: payload manifest's ownership class for the four merge targets. Those are covered by the argv-head
#: rule below instead, which is where they would actually be dangerous.
UNAMBIGUOUS_GIT_WRITE_VERBS = GIT_WRITE_VERBS - {
    "add", "tag", "am", "rm", "mv", "reset", "branch", "merge", "apply", "remote", "clean", "switch",
}


def backend_sources() -> list[Path]:
    return sorted(p for p in BACKEND.rglob("*.py") if "__pycache__" not in p.parts)


def parsed() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text("utf-8"), filename=str(p))) for p in backend_sources()]


def _code_strings(tree: ast.Module) -> list[tuple[int, str]]:
    """Every string constant that is *not* a docstring.

    Docstrings are excluded because this file's whole method is "executable references only": the
    modules explain the rules they follow, and quoting a forbidden name in an explanation must not be
    the thing that fails the build.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            out.append((node.lineno, node.value))
    return out


def _string_sequences(tree: ast.Module) -> list[tuple[int, list[str]]]:
    """List/tuple/set literals reduced to their string elements, with the line number."""
    out: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            continue
        strings = [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        if strings:
            out.append((node.lineno, strings))
    return out


# --------------------------------------------------------------------------- #
# no shell, anywhere
# --------------------------------------------------------------------------- #


def test_no_module_asks_for_a_shell():
    """``shell=True`` turns a repository path into shell source. There is no acceptable use of it."""
    offences = []
    for path in backend_sources():
        text = path.read_text("utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if "shell=True" in line and not line.lstrip().startswith("#"):
                offences.append(f"{path.name}:{number}")
    assert offences == [], offences


def test_every_shell_keyword_is_the_literal_false():
    """A computed ``shell=`` is the same hole with an extra step, so the value must be literal False."""
    offences = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "shell":
                    continue
                literal_false = (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                )
                if not literal_false:
                    offences.append(f"{path.name}:{node.lineno}")
    assert offences == [], offences


@pytest.mark.parametrize("forbidden", ["os.system", "os.popen", "os.execv", "os.spawnl", "os.spawnv"])
def test_no_module_shells_out_through_os(forbidden):
    for path in backend_sources():
        text = path.read_text("utf-8")
        lines = [
            f"{path.name}:{n}"
            for n, line in enumerate(text.splitlines(), start=1)
            if forbidden in line and not line.lstrip().startswith("#")
        ]
        assert lines == [], lines


# --------------------------------------------------------------------------- #
# one door per external tool
# --------------------------------------------------------------------------- #


def test_only_the_two_tool_modules_import_subprocess():
    importers = set()
    for path, tree in parsed():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "subprocess" for a in node.names):
                importers.add(path.name)
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "subprocess":
                importers.add(path.name)
    assert importers == SPAWN_MODULES, sorted(importers)


def test_only_the_two_tool_modules_spawn_a_child():
    """Catches the spawn itself, not the import: ``os.fork``/``multiprocessing`` would slip an import test."""
    spawn_attrs = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
    offences = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                name = f"{func.value.id}.{func.attr}"
            elif isinstance(func, ast.Name):
                name = func.id
            if name is None:
                continue
            spawning = (
                (name.startswith("subprocess.") and name.split(".", 1)[1] in spawn_attrs)
                or name in {"fork", "os.fork", "os.forkpty", "posix_spawn", "os.posix_spawn"}
                or name.startswith("multiprocessing.")
                or name in {"create_subprocess_exec", "create_subprocess_shell"}
                or name in {"asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}
            )
            if spawning and path.name not in SPAWN_MODULES:
                offences.append(f"{path.name}:{node.lineno}: {name}")
    assert offences == [], offences


def test_the_spawn_scan_would_catch_a_real_violation():
    """Not vacuous: the same visitor over a synthetic offence must find it."""
    tree = ast.parse('import subprocess\nsubprocess.run(["git", "commit"])\n')
    found = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]
    assert found == ["run"]


# --------------------------------------------------------------------------- #
# git stays read-only
# --------------------------------------------------------------------------- #


def test_no_unambiguous_git_write_verb_appears_as_a_string():
    """``push``/``commit``/``checkout`` have no other meaning here, so any literal is a finding."""
    offences = []
    for path, tree in parsed():
        if path.name in ("constants.py", "security.py"):
            continue  # the deny tables themselves
        for lineno, value in _code_strings(tree):
            if value in UNAMBIGUOUS_GIT_WRITE_VERBS:
                offences.append(f"{path.name}:{lineno}: {value!r}")
    assert offences == [], offences


def test_no_string_sequence_starts_with_a_git_write_verb():
    """The argv shape: ``["commit", "-m", …]`` is a write even when the verb is an ordinary word."""
    offences = []
    for path, tree in parsed():
        if path.name in ("constants.py", "security.py"):
            continue
        for lineno, strings in _string_sequences(tree):
            if strings and strings[0] in GIT_WRITE_VERBS:
                offences.append(f"{path.name}:{lineno}: {strings[:4]}")
    assert offences == [], offences


def test_the_git_scan_would_catch_a_real_violation():
    tree = ast.parse('argv = ["commit", "-m", "wip"]\n')
    heads = [s[0] for _lineno, s in _string_sequences(tree) if s]
    assert heads == ["commit"] and heads[0] in GIT_WRITE_VERBS


def test_the_production_write_verb_set_covers_this_files_copy(studio):
    """Two copies of a deny list only help while they agree — in the safe direction.

    Production may be *wider* (it also denies ``clone``, ``init``, ``bisect``, ``notes``, ``replace``,
    which §1.1 does not list); it may never be narrower, because every verb dropped from it becomes a
    verb ``assert_git_argv_readonly`` waves through.
    """
    assert GIT_WRITE_VERBS <= set(studio.constants.GIT_WRITE_VERBS)


# --------------------------------------------------------------------------- #
# AI-DLC's guardrails are never disabled
# --------------------------------------------------------------------------- #


def test_aidlc_skip_variables_appear_only_where_they_are_stripped():
    """``AIDLC_SKIP_*`` disables AI-DLC's own presence and artifact guards.

    ``constants.py`` names them so ``security.build_subprocess_env`` can assert they are absent from
    every child environment. A third module naming one would be Studio turning a guardrail off.
    """
    offences = []
    for path in backend_sources():
        if path.name in ENV_POLICY_MODULES:
            continue
        for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if "AIDLC_SKIP_" in line:
                offences.append(f"{path.name}:{number}: {line.strip()}")
    assert offences == [], offences


def test_the_forbidden_env_names_are_actually_asserted_absent(studio):
    """The exemption above is only safe because those two modules use the names to *refuse* them."""
    names = set(studio.constants.ENV_MUST_BE_ABSENT)
    assert {"AIDLC_SKIP_HUMAN_PRESENCE_GUARD", "AIDLC_SKIP_ARTIFACT_GUARD"} <= names
    env = studio.security.build_subprocess_env()
    assert not [k for k in env if k.startswith("AIDLC_") or k.startswith("CLAUDE_")]


# --------------------------------------------------------------------------- #
# the host bridge stays read-mostly
# --------------------------------------------------------------------------- #


def test_no_module_references_a_host_dispatch_function():
    """The human lane is two-phase: the backend records, the browser sends, the browser reports.

    Any executable reference to the host's chat internals would be a fourth party in that protocol —
    one that leaves no ``Delivering`` row, no ``HUMAN_TURN`` from the dashboard's own path, and no way
    to prove afterwards whether the decision was delivered.
    """
    offences = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in HOST_DISPATCH_NAMES:
                offences.append(f"{path.name}:{node.lineno}: name {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in HOST_DISPATCH_NAMES:
                offences.append(f"{path.name}:{node.lineno}: attribute .{node.attr}")
        # A name assembled as a string and fetched with getattr would defeat the AST walk above.
        for lineno, value in _code_strings(tree):
            if value in HOST_DISPATCH_NAMES:
                offences.append(f"{path.name}:{lineno}: literal {value!r}")
    assert offences == [], offences


def test_no_module_appends_a_row_to_a_host_slot():
    """``slot.append("user", …)`` writes the transcript the reconciler later reads as *evidence*.

    Studio forging that row would make its own delivery proof circular, so the only writer of a user
    row is the host itself, reached through the public chat API from the browser.
    """
    offences = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "append":
                continue
            receiver = node.func.value
            target = getattr(receiver, "id", None) or getattr(receiver, "attr", None) or ""
            if "slot" in str(target).lower():
                offences.append(f"{path.name}:{node.lineno}: {target}.append(...)")
    assert offences == [], offences


def test_the_bridge_never_imports_a_private_host_module():
    """Host symbols are resolved by name at call time (so a missing one is a capability, not a crash).

    A top-level ``from kiro_crew.dashboard.chat_runner import …`` would also make the app fail to load
    on a host that renamed the module — the exact fragility the capability probes exist to avoid.
    """
    offences = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            modules = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for module in modules:
                leaf = module.rsplit(".", 1)[-1]
                if module.startswith("kiro_crew.") and leaf.startswith("_"):
                    offences.append(f"{path.name}:{node.lineno}: {module}")
                if module.startswith("kiro_crew.dashboard.chat"):
                    offences.append(f"{path.name}:{node.lineno}: {module}")
    assert offences == [], offences


# --------------------------------------------------------------------------- #
# imports inside the package stay relative (the loader has no parent package on 0.3.0)
# --------------------------------------------------------------------------- #


def test_every_intra_package_import_is_relative():
    """An absolute ``import backend.studio.x`` cannot resolve: the app is loaded by file path.

    ``backend/routes.py`` and ``backend/hooks.py`` are exempt in the other direction — they must NOT use
    a relative import, which ``test_loader.py`` pins.
    """
    offences = []
    for path, tree in parsed():
        if path.parent == BACKEND:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module.split(".")[0] in {"backend", "studio", "_aidlc_studio_backend"}:
                    offences.append(f"{path.name}:{node.lineno}: from {module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in {"backend", "studio", "_aidlc_studio_backend"}:
                        offences.append(f"{path.name}:{node.lineno}: import {alias.name}")
    assert offences == [], offences


def test_the_entry_points_use_no_relative_import():
    """On the 0.3.0 loader there is no parent package, so a relative import here fails at enable time."""
    for name in ("routes.py", "hooks.py"):
        tree = ast.parse((BACKEND / name).read_text("utf-8"))
        levels = [n.level for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert all(level == 0 for level in levels), name


# --------------------------------------------------------------------------- #
# the UI never renders someone else's file as HTML
# --------------------------------------------------------------------------- #


def test_the_ui_never_sets_inner_html():
    """Artifact bodies and audit rows are third-party text rendered inside the dashboard's origin."""
    if not UI_SRC.is_dir():
        pytest.skip("ui/src is not present in this checkout")
    pattern = re.compile(r"dangerouslySetInnerHTML|innerHTML\s*=")
    offences = []
    for path in sorted(UI_SRC.rglob("*")):
        if path.suffix not in (".ts", ".tsx", ".js", ".jsx") or not path.is_file():
            continue
        for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if pattern.search(line):
                offences.append(f"{path.relative_to(APP_ROOT)}:{number}: {line.strip()}")
    assert offences == [], offences


# --------------------------------------------------------------------------- #
# every i18n key the backend emits resolves to real text
# --------------------------------------------------------------------------- #

I18N = APP_ROOT / "ui" / "src" / "i18n"


def _catalog(locale: str) -> dict[str, str]:
    import json

    path = I18N / f"{locale}.json"
    if not path.is_file():
        pytest.skip(f"{path.name} is not built in this checkout")
    return json.loads(path.read_text("utf-8"))


def _consequence_slots(node: ast.AST):
    """Every expression assigned to a consequence-variant slot: keyword argument or dict entry."""
    if isinstance(node, ast.keyword) and node.arg in ("consequence", "consequence_variant"):
        yield node.value
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "consequence_variant":
                yield value


def _value_literals(node: ast.AST):
    """String constants this expression can EVALUATE TO, ignoring ones it merely tests against.

    ``ast.walk`` is wrong here: ``"one_turn" if type == "run" else None`` reaches ``"run"``, which is a
    card type being compared, not a consequence variant. Descending only through value positions keeps
    the scan from reporting a phantom.
    """
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
    """A card with no copy renders a generic fallback, which tells the reader nothing about their work.

    This is the defect that shipped once: the backend sent ``action.<type>.headline`` while no catalogue
    part owned the ``action.*`` namespace, so all fourteen card types showed "Its summary is not
    available in this build." The build gate (``scripts/build_i18n.py``) checks the same thing; this
    test exists so a plain ``pytest`` run fails too, rather than only the full ``scripts/check.sh``.
    """
    constants = studio.constants
    for locale in ("en-US", "zh-CN"):
        catalog = _catalog(locale)
        missing = [
            f"action.{action_type}.headline"
            for action_type in constants.ACTION_TYPE
            if f"action.{action_type}.headline" not in catalog
        ]
        missing += [
            f"action.{action_type}.consequence.{variant}"
            for action_type, variants in constants.CARD_CONSEQUENCE_VARIANTS.items()
            for variant in variants
            if f"action.{action_type}.consequence.{variant}" not in catalog
        ]
        assert missing == [], f"{locale}: {missing}"


def test_the_declared_consequence_variants_cover_every_emission_site(studio):
    """``CARD_CONSEQUENCE_VARIANTS`` is only useful as a gate if it is a superset of what runs.

    Two things can emit a variant: the default table, and a call that passes ``consequence=`` explicitly
    to override it. Both are checked, because the override is precisely what a seeds-only gate misses.
    """
    declared = studio.constants.CARD_CONSEQUENCE_VARIANTS
    seeds = studio.projection.SEED_CONSEQUENCE

    for action_type, variant in seeds.items():
        if variant is None:
            continue
        assert variant in declared.get(action_type, ()), f"seed {action_type}={variant} undeclared"

    # Every literal handed to a `consequence=` / `consequence_variant=` slot anywhere in the backend.
    every_variant = {v for variants in declared.values() for v in variants}
    literals: list[str] = []
    for path, tree in parsed():
        for node in ast.walk(tree):
            for value in _consequence_slots(node):
                for leaf in _value_literals(value):
                    literals.append(f"{path.name}:{leaf.lineno}:{leaf.value}")

    # `__default__` is the sentinel meaning "use the seed table", not a variant name.
    undeclared = [
        entry
        for entry in literals
        if entry.rsplit(":", 1)[1] not in every_variant | {"__default__"}
    ]
    assert undeclared == [], undeclared
    assert literals, "the scan found no consequence literals at all — it has stopped working"
