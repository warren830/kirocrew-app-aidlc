"""Reviewed cleanup of terminal installer data, never of repository files.

Only generated backups/<txid> and failed/<txid> directories belonging to known transactions are
candidates. A digest binds selection, database protections and the complete bounded filesystem
inventory (including inode/ctime). Directory descriptors and O_NOFOLLOW keep deletion anchored.

Before deleting, a committed preferences record retains the reviewed entries and top-level JSON
evidence. Installer rows/receipts are never changed. A crash or partial failure leaves the remaining
tree in place; a fresh preview is required to authorize its new shape. There is no background sweep.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import StudioError

TERMINAL = frozenset({"committed", "rolled_back", "failed"})
CATEGORIES = ("backups", "failed")
TX_RE = re.compile(r"tx_[0-9a-f]{16}")
ENTRY_RE = re.compile(r"tx_[0-9a-f]{16}:(?:backups|failed)")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
MAX_HISTORY = 1000
MAX_SELECTION = 100
MAX_NODES = 20000
MAX_DEPTH = 40
MAX_METADATA_BYTES = 1 << 20
MAX_EVIDENCE_BYTES = 4 << 20
MAX_HISTORY_BYTES = 8 << 20
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _stamp(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return _stamp(value)[:3]


def selection(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_SELECTION:
        raise StudioError("bad_body", "entry_ids must be a bounded list",
                          details={"limit": MAX_SELECTION})
    if any(not isinstance(item, str) or not ENTRY_RE.fullmatch(item) for item in value):
        raise StudioError("bad_body", "entry_ids must be generated transaction/category identifiers")
    if len(set(value)) != len(value):
        raise StudioError("bad_body", "entry_ids must be unique")
    return sorted(value)


class _UnsafeTree(Exception):
    pass


@contextmanager
def _directory(name: str | Path, *, parent: int | None = None) -> Iterator[int]:
    fd = os.open(name, _DIR_FLAGS, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


class MaintenanceService:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.storage = services.storage
        self.data_dir = Path(services.ctx.data_dir).absolute()

    def _metadata(self, repo: Any) -> dict[str, Any]:
        row = self.storage.get("repos", repo.repo_id)
        if row is None:
            raise StudioError("repo_not_found", "repository is no longer registered")
        if row.get("resolved_identity") != repo.resolved_identity or not repo.resolved_identity:
            raise StudioError("identity_unprovable", "repository identity changed")
        transactions = self.storage.select(
            "install_transactions", {"repo_id": repo.repo_id},
            order_by="transaction_id ASC", limit=MAX_HISTORY + 1,
        )
        receipts = self.storage.select(
            "install_receipts", {"repo_id": repo.repo_id},
            order_by="receipt_id ASC", limit=MAX_HISTORY + 1,
        )
        if len(transactions) > MAX_HISTORY or len(receipts) > MAX_HISTORY:
            raise StudioError("too_large", "maintenance history exceeds the bounded scan",
                              details={"reason": "history_limit", "limit": MAX_HISTORY})
        if any(not TX_RE.fullmatch(str(tx["transaction_id"])) for tx in transactions):
            raise StudioError("state_inconsistent", "invalid transaction identifier in history")
        meta = {
            "repo": {key: row.get(key) for key in
                     ("id", "resolved_identity", "receipt_version", "install_status")},
            "transactions": transactions, "receipts": receipts,
        }
        if len(json.dumps(meta).encode()) > MAX_HISTORY_BYTES:
            raise StudioError("too_large", "maintenance history metadata exceeds the byte limit")
        return meta

    @staticmethod
    def _protections(meta: dict[str, Any]) -> dict[str, str]:
        protected: dict[str, str] = {}
        receipts = {r["receipt_id"]: r for r in meta["receipts"]}
        roots = []
        current = meta["repo"].get("receipt_version")
        if current:
            roots.append((current, "current_receipt"))
        roots.extend((r["receipt_id"], "current_receipt")
                     for r in receipts.values() if r["status"] == "current")
        for tx in meta["transactions"]:
            txid = tx["transaction_id"]
            if tx["status"] == "recovery_required":
                protected[txid] = "recovery_required"
            elif tx["status"] not in TERMINAL:
                protected[txid] = "active_transaction"
            elif not tx.get("finished_at"):
                protected[txid] = "unfinished_transaction"
            elif tx.get("resolved_repo_identity") != meta["repo"]["resolved_identity"]:
                protected[txid] = "identity_mismatch"
            if txid in protected and tx.get("prior_receipt_id"):
                roots.append((tx["prior_receipt_id"], "receipt_restoration"))
        visited = set()
        missing = False
        # Installer._rollback_material reads backups/<current.transaction_id>, not the target
        # receipt's transaction backup. Recovery restores its own transaction's journal/backup.
        # Preserve the immediate receipt an unsettled transaction may make current again, but do
        # not recurse through older receipts: those are optional historical restore points.
        for rid, reason in roots:
            if rid in visited:
                continue
            visited.add(rid)
            receipt = receipts.get(rid)
            if receipt is None:
                missing = True
                continue
            protected.setdefault(receipt["transaction_id"], reason)
        if missing or meta["repo"]["install_status"] == "recovery_required":
            reason = "receipt_reference_missing" if missing else "repo_recovery_required"
            for tx in meta["transactions"]:
                protected.setdefault(tx["transaction_id"], reason)
        return protected

    @contextmanager
    def _anchors(self, repo: Any) -> Iterator[dict[str, Any]]:
        # Canonical repo paths are registration data; never open or scan the repository.
        root = self.data_dir.resolve()
        repo_path = Path(repo.canonical_path)
        if root == repo_path or root.is_relative_to(repo_path) or repo_path.is_relative_to(root):
            raise StudioError("bad_path", "app data and repository paths overlap")
        with ExitStack() as stack:
            try:
                root_fd = stack.enter_context(_directory(self.data_dir))
                root_stat = os.fstat(root_fd)
                anchors: dict[str, Any] = {"root": root_fd, "root_stat": _identity(root_stat)}
                for category in CATEGORIES:
                    try:
                        fd = stack.enter_context(_directory(category, parent=root_fd))
                    except FileNotFoundError:
                        anchors[category] = None
                        continue
                    if os.fstat(fd).st_dev != root_stat.st_dev:
                        raise StudioError("bad_path", "maintenance root crosses a filesystem boundary")
                    anchors[category] = (fd, _identity(os.fstat(fd)))
            except OSError as exc:
                raise StudioError("bad_path", "maintenance directory is unavailable or indirect",
                                  details={"reason": "unsafe_data_root", "errno": exc.errno}) from exc
            yield anchors

    def _guard_anchors(self, anchors: dict[str, Any], category: str) -> None:
        if _identity(os.stat(self.data_dir, follow_symlinks=False)) != anchors["root_stat"]:
            raise _UnsafeTree("path_changed")
        fd, expected = anchors[category]
        if (_identity(os.stat(category, dir_fd=anchors["root"], follow_symlinks=False)) != expected
                or _identity(os.fstat(fd)) != expected):
            raise _UnsafeTree("path_changed")

    def _scan(self, parent: int, name: str, budget: list[int]) -> dict[str, Any]:
        nodes: dict[str, tuple[int, ...]] = {}
        evidence: dict[str, str] = {}
        total = files = 0

        def walk(fd: int, prefix: str, depth: int, device: int) -> None:
            nonlocal total, files
            if depth > MAX_DEPTH:
                raise _UnsafeTree("scan_limit")
            with os.scandir(fd) as entries:
                names = []
                for entry in entries:
                    budget[0] += 1
                    if budget[0] > MAX_NODES:
                        raise _UnsafeTree("scan_limit")
                    names.append(entry.name)
            for child in sorted(names):
                rel = f"{prefix}/{child}" if prefix else child
                st = os.stat(child, dir_fd=fd, follow_symlinks=False)
                nodes[rel] = _stamp(st)
                if st.st_dev != device:
                    raise _UnsafeTree("filesystem_boundary")
                if stat.S_ISLNK(st.st_mode):
                    raise _UnsafeTree("symlink")
                if stat.S_ISDIR(st.st_mode):
                    with _directory(child, parent=fd) as sub:
                        if _stamp(os.fstat(sub)) != _stamp(st):
                            raise _UnsafeTree("path_changed")
                        walk(sub, rel, depth + 1, device)
                elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                    total += st.st_size
                    files += 1
                    if not prefix and child.endswith(".json"):
                        if st.st_size > MAX_METADATA_BYTES or budget[1] + st.st_size > MAX_EVIDENCE_BYTES:
                            raise _UnsafeTree("evidence_limit")
                        handle = os.open(child, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                        with os.fdopen(handle, "rb") as stream:
                            if _stamp(os.fstat(stream.fileno())) != _stamp(st):
                                raise _UnsafeTree("path_changed")
                            data = stream.read(MAX_METADATA_BYTES + 1)
                            if len(data) != st.st_size or _stamp(os.fstat(stream.fileno())) != _stamp(st):
                                raise _UnsafeTree("path_changed")
                        evidence[child] = base64.b64encode(data).decode("ascii")
                        budget[1] += len(data)
                else:
                    raise _UnsafeTree("hardlink" if stat.S_ISREG(st.st_mode) else "special_file")
            if _stamp(os.fstat(fd)) != nodes[prefix]:
                raise _UnsafeTree("path_changed")

        st = os.stat(name, dir_fd=parent, follow_symlinks=False)
        nodes[""] = _stamp(st)
        if not stat.S_ISDIR(st.st_mode):
            raise _UnsafeTree("symlink" if stat.S_ISLNK(st.st_mode) else "not_directory")
        with _directory(name, parent=parent) as fd:
            if _stamp(os.fstat(fd)) != _stamp(st):
                raise _UnsafeTree("path_changed")
            walk(fd, "", 0, st.st_dev)
        return {"nodes": nodes, "size_bytes": total, "files": files, "evidence": evidence}

    def _plan(self, repo: Any, selected: list[str], anchors: dict[str, Any]) -> dict[str, Any]:
        meta = self._metadata(repo)
        protections = self._protections(meta)
        receipt_transactions = {r["transaction_id"] for r in meta["receipts"]}
        entries, trees = [], {}
        budget = [0, 0]
        for tx in meta["transactions"]:
            txid = tx["transaction_id"]
            for category in CATEGORIES:
                anchor = anchors[category]
                if anchor is None:
                    continue
                fd, _ = anchor
                try:
                    st = os.stat(txid, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                eid = f"{txid}:{category}"
                reason = protections.get(txid)
                expected_path = self.data_dir / category / txid
                stored = tx.get("backup_dir" if category == "backups" else "failed_dir")
                if stored and str(expected_path) != stored:
                    reason = reason or "path_mismatch"
                tree: dict[str, Any] = {"nodes": {"": _stamp(st)}, "size_bytes": None,
                                        "files": None, "evidence": {}}
                try:
                    self._guard_anchors(anchors, category)
                    tree = self._scan(fd, txid, budget)
                except _UnsafeTree as exc:
                    reason = reason or str(exc)
                except OSError:
                    reason = reason or "unreadable"
                protected = reason is not None
                consequence = None
                if not protected:
                    if category == "backups" and txid in receipt_transactions:
                        reason = "older_restore_point"
                        consequence = (
                            "Deleting this backup discards an older restore point. "
                            "The current rollback backup and receipt history are retained."
                        )
                    elif category == "backups":
                        reason = "terminal_backup"
                        consequence = (
                            "Deletes backup files for a finished transaction. "
                            "Transaction and receipt records are retained."
                        )
                    else:
                        reason = "terminal_failure_evidence"
                        consequence = (
                            "Deletes archived failure files. Transaction records and top-level "
                            "JSON evidence are retained in app data."
                        )
                trees[eid] = tree
                entries.append({
                    "id": eid, "txid": txid, "category": category,
                    "relative_path": f"{category}/{txid}", "status": tx["status"],
                    "size_bytes": tree["size_bytes"], "files": tree["files"],
                    "protected": protected, "reason": reason, "consequence": consequence,
                    "selected": eid in selected,
                })
        known = {e["id"] for e in entries}
        if set(selected) - known:
            raise StudioError("install_conflict", "selected cleanup entries are absent or unknown",
                              details={"reason": "selection_changed", "entry_ids": sorted(set(selected) - known)})
        blocked = [{"id": e["id"], "reason": e["reason"]} for e in entries if e["selected"] and e["protected"]]
        eligible = [e for e in entries if not e["protected"]]
        chosen = [e for e in entries if e["selected"]]
        plan = {
            "repo_id": repo.repo_id, "entries": entries, "entry_ids": selected,
            "can_cleanup": bool(selected) and not blocked, "blocked": blocked,
            "totals": {
                "entries": len(entries), "eligible_entries": len(eligible),
                "protected_entries": len(entries) - len(eligible),
                "selected_entries": len(chosen),
                "eligible_bytes": sum(e["size_bytes"] or 0 for e in eligible),
                "selected_bytes": sum(e["size_bytes"] or 0 for e in chosen),
                "selected_files": sum(e["files"] or 0 for e in chosen),
                "unknown_sizes": sum(e["size_bytes"] is None for e in entries),
            },
        }
        plan["plan_digest"] = _digest({
            "plan": plan, "metadata": meta,
            "roots": {key: value[1] if key in CATEGORIES and value else value
                      for key, value in anchors.items() if key != "root"},
            "trees": trees,
        })
        return {"public": plan, "metadata": meta, "trees": trees}

    async def preview(self, repo: Any, entry_ids: Any = None) -> dict[str, Any]:
        selected = selection([] if entry_ids is None else entry_ids)

        def read() -> dict[str, Any]:
            with self.storage._txn(), self._anchors(repo) as anchors:
                return self._plan(repo, selected, anchors)["public"]

        return await asyncio.to_thread(read)

    def _delete(self, anchors: dict[str, Any], eid: str, tree: dict[str, Any]) -> None:
        txid, category = eid.split(":")
        parent, _ = anchors[category]
        nodes = tree["nodes"]
        links: list[tuple[int, str, tuple[int, ...]]] = []
        children: dict[str, list[str]] = {}
        for rel in nodes:
            if rel:
                base, _, name = rel.rpartition("/")
                children.setdefault(base, []).append(name)

        def guard() -> None:
            self._guard_anchors(anchors, category)
            for pfd, name, expected in links:
                if _identity(os.stat(name, dir_fd=pfd, follow_symlinks=False)) != expected:
                    raise _UnsafeTree("path_changed")

        def remove(pfd: int, name: str, rel: str) -> None:
            guard()
            expected = nodes[rel]
            if _stamp(os.stat(name, dir_fd=pfd, follow_symlinks=False)) != expected:
                raise _UnsafeTree("path_changed")
            with _directory(name, parent=pfd) as fd:
                if _stamp(os.fstat(fd)) != expected:
                    raise _UnsafeTree("path_changed")
                links.append((pfd, name, expected[:3]))
                for child in sorted(children.get(rel, [])):
                    path = f"{rel}/{child}" if rel else child
                    if stat.S_ISDIR(nodes[path][2]):
                        remove(fd, child, path)
                    else:
                        guard()
                        if _stamp(os.stat(child, dir_fd=fd, follow_symlinks=False)) != nodes[path]:
                            raise _UnsafeTree("path_changed")
                        os.unlink(child, dir_fd=fd)
                guard()
                # Unexpected files are never enumerated for deletion. rmdir refuses a nonempty tree.
                os.rmdir(name, dir_fd=pfd)
                links.pop()

        remove(parent, txid, "")

    def _cleanup_sync(self, repo: Any, selected: list[str], digest: str) -> dict[str, Any]:
        key = f"maintenance:{repo.repo_id}:{digest}"
        with self._anchors(repo) as anchors:
            with self.storage._txn():
                reviewed = self._plan(repo, selected, anchors)
                plan = reviewed["public"]
                if plan["plan_digest"] != digest:
                    raise StudioError("install_conflict", "cleanup preview changed",
                                      details={"reason": "plan_digest_mismatch"})
                if plan["blocked"]:
                    code = ("install_recovery_required" if any("recovery" in b["reason"] for b in plan["blocked"])
                            else "install_conflict")
                    raise StudioError(code, "protected installer evidence cannot be deleted",
                                      details={"reason": "protected_entries", "blocked": plan["blocked"]})
                record = {
                    "status": "prepared", "plan": plan, "at": self.services.clock.iso(),
                    "evidence": {eid: reviewed["trees"][eid]["evidence"] for eid in selected},
                }
                # Commit this before any unlink. A crash cannot erase the transaction's JSON journal.
                self.storage.pref_set(key, record)
            with self.storage._txn():
                # Installer can reserve/update rows before acquiring its lease. Keep metadata and
                # deletion indivisible with the same nested transaction seam the installer uses.
                current = self._plan(repo, selected, anchors)
                if current["public"]["plan_digest"] != digest:
                    raise StudioError("install_conflict", "cleanup changed before deletion",
                                      details={"reason": "plan_digest_mismatch"})
                deleted, failures = [], []
                for eid in selected:
                    try:
                        # Recheck even nested same-thread callbacks/fault injection before each entry.
                        if _digest(self._metadata(repo)) != _digest(reviewed["metadata"]):
                            raise _UnsafeTree("metadata_changed")
                        self._delete(anchors, eid, reviewed["trees"][eid])
                        deleted.append(eid)
                    except (OSError, _UnsafeTree) as exc:
                        failures.append({"id": eid, "reason": str(exc) if isinstance(exc, _UnsafeTree)
                                         else "delete_failed", "errno": getattr(exc, "errno", None)})
                        break
                result = {
                    "ok": not failures, "plan_digest": digest, "deleted": deleted,
                    "failures": failures,
                    "remaining": [eid for eid in selected if eid not in deleted],
                    "record_key": key, "requires_preview": bool(failures),
                }
                self.storage.pref_set(key, {**record, "status": "partial" if failures else "complete",
                                            "result": result})
                return result

    async def cleanup(self, repo: Any, entry_ids: Any, plan_digest: Any) -> dict[str, Any]:
        selected = selection(entry_ids)
        if not selected or not isinstance(plan_digest, str) or not DIGEST_RE.fullmatch(plan_digest):
            raise StudioError("bad_body", "select entries and supply their exact preview plan_digest")

        async def work() -> dict[str, Any]:
            grant = await self.services.scheduler.acquire_admin(
                repo, operation_type="repo_metadata", transaction_id=None,
            )
            try:
                result = await asyncio.to_thread(self._cleanup_sync, repo, selected, plan_digest)
                await self.services.events.publish("repo.updated", {"repo_id": repo.repo_id})
                return result
            finally:
                await self.services.scheduler.release(grant)

        # Client disconnects do not free the lease while the filesystem worker is still deleting.
        task = asyncio.create_task(work())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            # A second disconnect/shutdown cancellation must not cancel work's to_thread await
            # and release the lease while its filesystem worker still runs.
            try:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                task.result()
            finally:
                raise cancelled
