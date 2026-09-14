"""Path containment, bounded reads, redaction and subprocess hygiene.

Everything Studio reads belongs to someone else: a user's repository, AI-DLC's own audit trail, git's
output. This module is the single place that decides whether a read is allowed, how much of it is
allowed, and what may leave the process.

Four rules it enforces, each because the alternative is a real failure:

1. **Containment beats convenience.** A path is resolved and proven inside the registered repository
   root before it is opened. Symlinks are resolved first, so a link pointing outside the repo is
   refused rather than followed.
2. **Refuse, never truncate.** Half a state file parses into a confidently wrong projection, so an
   oversized file is skipped with a reason instead of read partially. Audit shards are the one
   exception: they are append-only and unbounded, so they are TAIL-read and the partial first line is
   dropped.
3. **Fail closed when the host's security module is unavailable.** Without
   ``kiro_crew.security.is_sensitive_path`` there is no way to know whether a path is protected, so
   every path is treated as protected.
4. **Prove bypass variables are absent.** AI-DLC reads its guard switches from the environment.
   Studio must not merely avoid setting them; it asserts they are not inherited, because an operator
   who exported one in the gateway's shell would otherwise silently disable the anti-forgery guard
   for every process Studio starts.

All functions are synchronous and cheap. The ones that touch the filesystem are called from worker
threads (``asyncio.to_thread``) like every other blocking read in the backend.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import constants as C
from .errors import StudioError

# --------------------------------------------------------------------------- #
# host security module, with a fail-closed fallback
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - the host module is always present in production
    from kiro_crew.security import is_sensitive_path as _host_is_sensitive
    from kiro_crew.security import redact_credentials as _host_redact

    HAS_HOST_SECURITY = True
except Exception:  # pragma: no cover
    HAS_HOST_SECURITY = False
    _host_is_sensitive = None  # type: ignore[assignment]
    _host_redact = None  # type: ignore[assignment]

#: Last-resort redaction when the host module cannot be imported. Deliberately broad: it is better to
#: mask a harmless string than to leak a key into an export or a Slack message.
_FALLBACK_SECRET_RE = re.compile(
    r"(?:(?:AKIA|ASIA)[0-9A-Z]{16})"
    r"|(?:(?i:bearer)\s+[A-Za-z0-9._\-]{16,})"
    r"|(?:(?i:api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,})"
    r"|(?:gh[pousr]_[A-Za-z0-9]{16,})"
    r"|(?:xox[baprs]-[A-Za-z0-9-]{10,})"
    r"|(?:-{5}BEGIN[A-Z ]*PRIVATE KEY-{5}[\s\S]*?-{5}END[A-Z ]*PRIVATE KEY-{5})"
)

#: Files Studio never reads and never lists, whatever a caller asks for. Reading them would put a
#: capability or a usage ledger into a projection that is rendered, exported and possibly sent to Slack.
SECRET_FILENAMES = (
    ".aidlc-steering-token-key",
    "usage-ledger.json",
    ".app_secret",
    ".local_secret",
    ".env",
    "credentials",
    "id_rsa",
    "id_ed25519",
)


def is_secret_file(path: Path) -> bool:
    return path.name in SECRET_FILENAMES


# --------------------------------------------------------------------------- #
# containment
# --------------------------------------------------------------------------- #


def realpath(path: Path | str) -> Path:
    """``os.path.realpath`` as a ``Path``, never raising for a missing component."""
    return Path(os.path.realpath(str(path)))


def resolve_inside(root: Path, rel: str | Path, *, must_exist: bool = False) -> Path:
    """Resolve ``rel`` under ``root`` and prove the result stays inside ``root``.

    Raises ``StudioError("bad_path")`` for an absolute or NUL-bearing input, for anything that escapes
    the root once symlinks are resolved, and — when ``must_exist`` — for a path that is not there.
    The comparison is done on both realpaths so a symlinked repository root still matches.
    """
    text = str(rel)
    if "\x00" in text:
        raise StudioError("bad_path", "path contains a NUL byte")
    candidate = Path(text)
    if candidate.is_absolute():
        raise StudioError("bad_path", "path must be relative to the repository root")
    root_real = realpath(root)
    resolved = realpath(root_real / candidate)
    if resolved != root_real and not resolved.is_relative_to(root_real):
        raise StudioError("bad_path", "path escapes the repository root")
    if must_exist and not resolved.exists():
        raise StudioError("bad_path", "path does not exist")
    return resolved


def validate_repo_root(raw: str) -> Path:
    """Accept only an absolute, existing, non-sensitive directory as a repository root.

    This is the only door into the filesystem for the whole product: a repository is registered by an
    explicit user path and nothing else is ever scanned (FR-REP-002).
    """
    text = (raw or "").strip()
    if not text:
        raise StudioError("bad_path", "path is required")
    if "\x00" in text:
        raise StudioError("bad_path", "path contains a NUL byte")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise StudioError("bad_path", "path must be absolute")
    resolved = realpath(path)
    if not resolved.is_dir():
        raise StudioError("bad_path", "path is not an existing directory")
    if is_sensitive(resolved):
        raise StudioError("sensitive_path", "path is protected and cannot be read")
    return resolved


def is_sensitive(path: Path) -> bool:
    """Whether the host considers this path protected. Fails closed when it cannot tell."""
    if not HAS_HOST_SECURITY or _host_is_sensitive is None:
        return True
    try:
        return bool(_host_is_sensitive(str(path)))
    except Exception:  # pragma: no cover - defensive
        return True


def is_regular_file(path: Path) -> bool:
    """A real file, not a symlink, FIFO, device or directory."""
    try:
        st = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


# --------------------------------------------------------------------------- #
# bounded reads
# --------------------------------------------------------------------------- #


def bounded_read(path: Path, cap: int) -> bytes | None:
    """Whole-file bytes, or ``None`` when absent, oversized, secret, or unreadable.

    ``None`` is a deliberate refusal, not an error: callers report "not read (too large)" so the user
    sees why a card is missing evidence instead of seeing a plausible partial parse.
    """
    if is_secret_file(path) or not is_regular_file(path):
        return None
    try:
        if path.stat().st_size > cap:
            return None
        return path.read_bytes()
    except OSError:
        return None


def bounded_text(path: Path, cap: int) -> str | None:
    raw = bounded_read(path, cap)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


def tail_read(path: Path, cap: int) -> bytes:
    """Last ``cap`` bytes of a file with the partial first line dropped.

    For append-only audit shards, where the whole file may be megabytes but only the tail matters.
    """
    if is_secret_file(path) or not is_regular_file(path):
        return b""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > cap:
                fh.seek(size - cap)
                fh.readline()  # discard the line the seek landed inside
            return fh.read()
    except OSError:
        return b""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str | None:
    """Digest a file in chunks, or ``None`` when it cannot be read."""
    if not is_regular_file(path):
        return None
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def composite_token(*parts: str) -> str:
    """Stable digest of ``":"``-joined string parts (boundary tokens, dedupe keys, fingerprints)."""
    return sha256_text(":".join(parts))


# --------------------------------------------------------------------------- #
# redaction
# --------------------------------------------------------------------------- #


def redact(text: str) -> str:
    """Mask credentials in text that will be shown, logged, notified or exported.

    Both scanners run: the host's (so Studio inherits every pattern KiroCrew learns about) and then
    Studio's own, which catches shapes the host currently lets through such as
    ``token=<hex>``. Studio's floor must be at least as strict as the host's because this text can
    reach a Slack message or a diagnostics bundle, where a leak is permanent.
    """
    if not text:
        return text
    out = text
    if HAS_HOST_SECURITY and _host_redact is not None:
        try:
            result = _host_redact(out)
            # The host returns (text, findings) in current versions, a bare string in older ones.
            out = str(result[0]) if isinstance(result, tuple) else str(result)
        except Exception:  # pragma: no cover - defensive
            pass
    return _FALLBACK_SECRET_RE.sub("<redacted>", out)


def redact_json(obj: Any) -> Any:
    """Recursively redact every string leaf of a JSON-shaped structure."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_json(v) for v in obj]
    return obj


_ABS_PATH_RE = re.compile(r"(?:/[^\s\"'`,;:()\[\]{}]+){2,}")


def scrub_repo_paths(text: str, allowed_roots: Sequence[str]) -> str:
    """Replace absolute paths that are not under an allowed root with ``<path>``.

    Used by the diagnostics export so a bundle about one repository cannot carry the layout of the
    user's whole disk (FR-EVT-007).
    """
    if not text:
        return text
    roots = tuple(str(realpath(r)) for r in allowed_roots)

    def repl(match: re.Match[str]) -> str:
        value = match.group(0)
        return value if any(value.startswith(root) for root in roots) else "<path>"

    return _ABS_PATH_RE.sub(repl, text)


# --------------------------------------------------------------------------- #
# subprocess hygiene
# --------------------------------------------------------------------------- #


def build_subprocess_env() -> dict[str, str]:
    """A minimal environment for every child process Studio starts.

    Keeps only what a CLI genuinely needs, drops every AI-DLC/Kiro/KiroCrew variable, and refuses to
    build an environment at all if a bypass switch is present in the parent — that condition is a
    configuration problem the operator must see, not something to silently work around.
    """
    present = [k for k in C.ENV_MUST_BE_ABSENT if k in os.environ]
    if present:
        raise StudioError(
            "internal_error",
            "refusing to start a child process while AI-DLC guard-bypass variables are set",
            details={"variables": present},
        )
    env = {k: os.environ[k] for k in C.ENV_KEEP if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env["NO_COLOR"] = "1"
    leaked = [k for k in env if k.startswith(C.ENV_FORBIDDEN_PREFIXES)]
    if leaked:  # pragma: no cover - ENV_KEEP contains no such name
        raise StudioError("internal_error", "forbidden variable leaked into the child environment",
                          details={"variables": leaked})
    return env


def assert_git_argv_readonly(argv: Sequence[str]) -> None:
    """Refuse any git invocation that is not a read of the repository it is run in (§1.3, review P21).

    ``argv`` EXCLUDES the binary: the verb is ``argv[0]``, because ``GitObserver`` prepends ``git_path``
    when it spawns. Anything before the verb is a global option, and that is where the dangerous ones
    live — ``-c core.fsmonitor=…`` runs a command, ``-C``/``--git-dir``/``--work-tree`` point git at
    another repository, ``--exec-path`` replaces its helpers, ``--output`` writes a file. So a leading
    ``-`` is refused outright rather than parsed, and every token is scanned for the deny list, which
    also catches the ``--flag=value`` spellings.

    A write verb is refused wherever it appears, not only in slot 0: that rejects a legitimate command
    whose *argument* happens to be spelled ``add``, which is the safe direction. ``git --version`` has
    no verb at all and is therefore not expressible here; ``GitObserver`` matches that one argv exactly
    and spawns it without a repository (its own exemption, so this rule stays "a read names a verb").
    """
    tokens = [str(token) for token in argv]
    if not tokens:
        raise StudioError("internal_error", "git invocation carries no subcommand")
    verb = tokens[0]
    if verb.startswith("-"):
        raise StudioError(
            "internal_error", f"git global option {verb!r} may not precede the subcommand"
        )
    if verb in C.GIT_WRITE_VERBS:
        raise StudioError("internal_error", f"git write verb {verb!r} is never permitted")
    if verb not in C.GIT_READ_VERBS:
        raise StudioError("internal_error", f"git verb {verb!r} is not on the read allowlist")
    for token in tokens:
        if "\x00" in token:
            raise StudioError("internal_error", "git argument contains a NUL byte")
        if token in C.GIT_FORBIDDEN_TOKENS or token.startswith(C.GIT_FORBIDDEN_PREFIXES):
            raise StudioError("internal_error", f"git option {token!r} is never permitted")
        if token in C.GIT_WRITE_VERBS:
            raise StudioError("internal_error", f"git write verb {token!r} is never permitted")


def assert_engine_argv_allowed(tool: str, verb: str, *, admin: bool) -> None:
    """Refuse any AI-DLC engine invocation outside the allowlist for this lane.

    ``admin=False`` is the unattended read lane: only stdout-producing verbs that mutate nothing.
    ``admin=True`` is an explicit user operation holding the repository admin lease. Transition verbs,
    audit appenders and repair verbs are on neither list, and this function proves it at call time.
    """
    if tool in C.ENGINE_FORBIDDEN_TOOLS:
        raise StudioError("internal_error", f"{tool} is never invoked by Studio")
    table = C.ENGINE_ADMIN_VERBS if admin else C.ENGINE_READ_VERBS
    allowed = set(table.get(tool, ()))
    # A verb spelled the way an older engine spells it is allowed exactly when its canonical name is.
    for canonical, spellings in C.ENGINE_VERB_ALIASES.items():
        if canonical in allowed:
            allowed.update(spellings)
    if verb in C.ENGINE_FORBIDDEN_VERBS and verb not in allowed:
        raise StudioError("internal_error", f"engine verb {verb!r} is never invoked by Studio")
    if verb in C.ENGINE_UNAVAILABLE_VERBS:
        raise StudioError(
            "engine_unavailable",
            f"the engine's own {verb!r} verb is not usable; Studio performs this itself",
        )
    if verb not in allowed:
        lane = "admin" if admin else "read-only"
        raise StudioError(
            "internal_error",
            f"engine verb {tool} {verb!r} is not on the {lane} allowlist",
        )


def truncate_output(raw: bytes, cap: int) -> str:
    """Decode and cap subprocess output, marking a truncation explicitly."""
    if len(raw) <= cap:
        return raw.decode("utf-8", errors="replace")
    head = raw[:cap].decode("utf-8", errors="replace")
    return head + f"\n… output truncated at {cap} bytes"


def iter_regular_files(root: Path, *, max_files: int, skip_dirs: Iterable[str] = ()) -> list[Path]:
    """Bounded, symlink-free walk used for artifact enumeration.

    Sorted for determinism, capped by count, and it never descends into a symlinked directory or the
    named runtime directories, so one huge or looping tree cannot stall a projection.
    """
    skip = {*skip_dirs}
    out: list[Path] = []
    stack = [root]
    while stack and len(out) < max_files:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if len(out) >= max_files:
                break
            name = entry.name
            try:
                st = entry.lstat()
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                if name in skip:
                    continue
                stack.append(entry)
            elif stat.S_ISREG(st.st_mode) and not is_secret_file(entry):
                out.append(entry)
    return sorted(out)
