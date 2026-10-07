"""Path containment, bounded reads, redaction and subprocess hygiene — the one place that decides whether a
read is allowed, how much of it, and what may leave the process.

* **Containment beats convenience.** A path is resolved (symlinks included) and proven inside the
  registered root before it is opened, so a link pointing out of the repository is refused, not followed.
* **Refuse, never truncate.** Half a state file parses into a confidently wrong projection, so an oversized
  file is skipped with a reason. Append-only audit shards are the exception: they are tail-read.
* **Fail closed without the host's security module.** Without ``is_sensitive_path`` there is no way to know
  whether a path is protected, so every path is.
* **Prove bypass variables are absent.** AI-DLC reads its guard switches from the environment; an operator
  who exported one in the gateway's shell would otherwise disable the anti-forgery guard for every child.

All functions are synchronous; the ones that touch the filesystem run on worker threads like every other
blocking read.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import constants as C
from .errors import StudioError

try:  # pragma: no cover - the host module is always present in production
    from kiro_crew.security import is_sensitive_path as _host_is_sensitive
    from kiro_crew.security import redact_credentials as _host_redact

    HAS_HOST_SECURITY = True
except Exception:  # pragma: no cover
    HAS_HOST_SECURITY = False
    _host_is_sensitive = None  # type: ignore[assignment]
    _host_redact = None  # type: ignore[assignment]

#: Studio's own redaction floor, applied after the host's. Deliberately broad: masking a harmless string is
#: better than leaking a key into an export or a Slack message, and it catches shapes the host lets through.
_FALLBACK_SECRET_RE = re.compile(
    r"(?:(?:AKIA|ASIA)[0-9A-Z]{16})"
    r"|(?:(?i:bearer)\s+[A-Za-z0-9._\-]{16,})"
    r"|(?:(?i:api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,})"
    r"|(?:gh[pousr]_[A-Za-z0-9]{16,})"
    r"|(?:xox[baprs]-[A-Za-z0-9-]{10,})"
    r"|(?:-{5}BEGIN[A-Z ]*PRIVATE KEY-{5}[\s\S]*?-{5}END[A-Z ]*PRIVATE KEY-{5})"
)

#: Files Studio never reads and never lists: a capability or a usage ledger in a projection would be
#: rendered, exported and possibly sent to Slack.
SECRET_FILENAMES = (
    ".aidlc-steering-token-key", "usage-ledger.json", ".app_secret", ".local_secret", ".env", "credentials",
    "id_rsa", "id_ed25519",
)


def is_secret_file(path: Path) -> bool:
    return path.name in SECRET_FILENAMES


# --------------------------------------------------------------------------- #
# containment
# --------------------------------------------------------------------------- #


def realpath(path: Path | str) -> Path:
    """``os.path.realpath`` as a ``Path``, never raising for a missing component."""
    return Path(os.path.realpath(str(path)))


def _no_nul(text: str) -> str:
    if "\x00" in text:
        raise StudioError("bad_path", "path contains a NUL byte")
    return text


def resolve_inside(root: Path, rel: str | Path, *, must_exist: bool = False) -> Path:
    """Resolve ``rel`` under ``root`` and prove the result stays inside it, or raise ``bad_path``.

    Both sides are realpaths, so a symlinked repository root still matches.
    """
    candidate = Path(_no_nul(str(rel)))
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
    """An absolute, existing, non-sensitive directory — the only door into the filesystem: a repository is
    registered by an explicit user path and nothing else is ever scanned (FR-REP-002)."""
    text = _no_nul((raw or "").strip())
    if not text:
        raise StudioError("bad_path", "path is required")
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
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# bounded reads
# --------------------------------------------------------------------------- #


def bounded_read(path: Path, cap: int) -> bytes | None:
    """Whole-file bytes, or ``None`` when absent, oversized, secret or unreadable — a refusal the caller
    reports ("not read: too large") instead of a plausible partial parse."""
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
    return None if raw is None else raw.decode("utf-8", errors="replace")


def tail_read(path: Path, cap: int) -> bytes:
    """The last ``cap`` bytes with the partial first line dropped (append-only audit shards)."""
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
    """Digest a regular file in chunks, or ``None`` when it cannot be read."""
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

    The host's scanner first (Studio inherits every pattern KiroCrew learns), then Studio's own floor:
    this text can reach a Slack message or a diagnostics bundle, where a leak is permanent.
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


def redact_json(obj: Any, roots: Sequence[str] | None = None) -> Any:
    """Redact every string leaf of a JSON-shaped value; with ``roots``, also scrub paths outside them."""
    if isinstance(obj, str):
        text = redact(obj)
        return text if roots is None else scrub_repo_paths(text, roots)
    if isinstance(obj, Mapping):
        return {str(k): redact_json(v, roots) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_json(v, roots) for v in obj]
    return obj


_ABS_PATH_RE = re.compile(r"(?:/[^\s\"'`,;:()\[\]{}]+){2,}")


def scrub_repo_paths(text: str, allowed_roots: Sequence[str]) -> str:
    """Replace absolute paths not under an allowed root with ``<path>``, so a bundle about one repository
    cannot carry the layout of the user's whole disk (FR-EVT-007)."""
    if not text:
        return text
    roots = tuple(str(realpath(r)) for r in allowed_roots)
    return _ABS_PATH_RE.sub(
        lambda m: m.group(0) if any(m.group(0).startswith(root) for root in roots) else "<path>", text
    )


# --------------------------------------------------------------------------- #
# subprocess hygiene
# --------------------------------------------------------------------------- #


def build_subprocess_env() -> dict[str, str]:
    """A minimal environment for every child process: only what a CLI needs, nothing AI-DLC/Kiro/KiroCrew.

    A bypass switch in the parent is a configuration problem the operator must see, so it refuses to build
    an environment at all rather than silently working around it.
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
    """Refuse any git invocation that is not a read of the repository it runs in (§1.3, review P21).

    ``argv`` excludes the binary, so the verb is ``argv[0]``. Anything before the verb is a global option and
    that is where the dangerous ones live (``-c core.fsmonitor=…`` runs a command, ``-C``/``--git-dir`` point
    at another repository, ``--output`` writes a file), so a leading ``-`` is refused rather than parsed and
    every token is scanned for the deny list, ``--flag=value`` spellings included. A write verb is refused
    wherever it appears — rejecting an argument that happens to be spelled ``add`` is the safe direction.
    """
    tokens = [str(token) for token in argv]
    if not tokens:
        raise StudioError("internal_error", "git invocation carries no subcommand")
    verb = tokens[0]
    if verb.startswith("-"):
        raise StudioError("internal_error", f"git global option {verb!r} may not precede the subcommand")
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
    """Refuse any AI-DLC engine invocation outside this lane's allowlist.

    ``admin=False`` is the read lane (stdout-producing verbs that mutate nothing); ``admin=True`` an explicit
    user operation holding the admin lease. Transition verbs, audit appenders and repair verbs are on neither.
    """
    if tool in C.ENGINE_FORBIDDEN_TOOLS:
        raise StudioError("internal_error", f"{tool} is never invoked by Studio")
    allowed = set((C.ENGINE_ADMIN_VERBS if admin else C.ENGINE_READ_VERBS).get(tool, ()))
    # A verb spelled the way an older engine spells it is allowed exactly when its canonical name is.
    for canonical, spellings in C.ENGINE_VERB_ALIASES.items():
        if canonical in allowed:
            allowed.update(spellings)
    if verb in C.ENGINE_FORBIDDEN_VERBS and verb not in allowed:
        raise StudioError("internal_error", f"engine verb {verb!r} is never invoked by Studio")
    if verb in C.ENGINE_UNAVAILABLE_VERBS:
        raise StudioError("engine_unavailable", f"the engine's own {verb!r} verb is not usable; Studio performs this itself")
    if verb not in allowed:
        lane = "admin" if admin else "read-only"
        raise StudioError("internal_error", f"engine verb {tool} {verb!r} is not on the {lane} allowlist")


def truncate_output(raw: bytes, cap: int) -> str:
    """Decode and cap subprocess output, marking a truncation explicitly."""
    if len(raw) <= cap:
        return raw.decode("utf-8", errors="replace")
    return raw[:cap].decode("utf-8", errors="replace") + f"\n… output truncated at {cap} bytes"


def iter_regular_files(root: Path, *, max_files: int, skip_dirs: Iterable[str] = ()) -> list[Path]:
    """A bounded, symlink-free walk for artifact enumeration: sorted, capped by count, never descending into
    a symlinked directory or a named runtime directory, so one huge or looping tree cannot stall a projection."""
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
            try:
                mode = entry.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISDIR(mode):
                if entry.name not in skip:
                    stack.append(entry)
            elif stat.S_ISREG(mode) and not is_secret_file(entry):
                out.append(entry)
    return sorted(out)
