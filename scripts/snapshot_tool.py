#!/usr/bin/env python3
"""Create and verify frozen, scoped review artifacts for ContractV2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from contract_tool import (
    CONTRACT,
    ContractError,
    DOMAINS,
    canonical_json,
    canonicalize_record,
    digest_record,
    load_json_bytes,
    load_json_file,
    normalize_repo_path,
    validate_record,
)


SNAPSHOT_MANIFEST = CONTRACT["artifact_contracts"]["snapshot_manifest"]
MANIFEST_VERSION = SNAPSHOT_MANIFEST["version"]
MANIFEST_FIELDS = frozenset(SNAPSHOT_MANIFEST["fields"])
GIT_IDENTITY_FIELDS = frozenset(SNAPSHOT_MANIFEST["git_identity_fields"])
ENTRY_CONTRACT = SNAPSHOT_MANIFEST["entry"]
ENTRY_FIELDS = frozenset(ENTRY_CONTRACT["fields"])
ENTRY_STATUSES = frozenset(ENTRY_CONTRACT["statuses"])
ENTRY_TYPES = frozenset(ENTRY_CONTRACT["types"])
ARTIFACT_PREFIXES = dict(ENTRY_CONTRACT["artifact_prefixes"])
BASELINE_TYPES = frozenset(SNAPSHOT_MANIFEST["baseline_types"])
TOP_LEVEL = frozenset(SNAPSHOT_MANIFEST["top_level"])


def _contract_limit(reference: str) -> int:
    current: Any = CONTRACT
    for part in reference.split("."):
        if not isinstance(current, dict) or part not in current:
            raise SnapshotError(f"unknown artifact contract limit reference: {reference}")
        current = current[part]
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        raise SnapshotError(f"artifact contract limit is not a non-negative integer: {reference}")
    return current


def _artifact_limit(name: str) -> int:
    declaration = SNAPSHOT_MANIFEST["limits"][name]
    if isinstance(declaration, str):
        return _contract_limit(declaration)
    if not isinstance(declaration, dict):
        raise SnapshotError(f"artifact contract limit declaration is invalid: {name}")
    reference = declaration.get("limit_ref")
    multiplier = declaration.get("multiplier", 1)
    if not isinstance(reference, str) or isinstance(multiplier, bool) or not isinstance(multiplier, int) or multiplier < 0:
        raise SnapshotError(f"artifact contract limit declaration is invalid: {name}")
    return _contract_limit(reference) * multiplier


SNAPSHOT_DEPTH = _artifact_limit("snapshot_depth_ref")
SYMLINK_TARGET_BYTES = _artifact_limit("symlink_target_bytes_ref")
MANIFEST_BYTES = _artifact_limit("manifest_bytes_ref")
MAX_ENTRIES = _artifact_limit("max_entries")
HEX64 = set("0123456789abcdef")
GIT_HEAD_LENGTHS = {40, 64}


class SnapshotError(ValueError):
    """An invalid snapshot input or artifact."""


def _digest(data: bytes, domain: str) -> str:
    return hashlib.sha256(DOMAINS[domain] + data).hexdigest()


def _ensure_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - HEX64:
        raise SnapshotError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _run_git(root: Path, *args: str) -> bytes:
    command = ["git", "--no-optional-locks", "--literal-pathspecs", "-C", str(root), *args]
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    except OSError as exc:
        raise SnapshotError(f"cannot run Git identity query: {exc}") from exc
    if result.returncode != 0:
        raise SnapshotError(f"Git identity query failed: {' '.join(args)}")
    return result.stdout


def git_identity(root: Path) -> dict[str, str]:
    inside = _run_git(root, "rev-parse", "--is-inside-work-tree").decode("utf-8", errors="strict").strip()
    if inside != "true":
        raise SnapshotError("repository root is not a Git worktree")
    try:
        head = _run_git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    except SnapshotError:
        # An empty repository has no HEAD; verify that this is the only reason for
        # the failed probe before using an explicit non-commit sentinel.
        if _run_git(root, "rev-list", "--all").strip():
            raise
        head = "UNCOMMITTED"
    branch_name = _run_git(root, "rev-parse", "--abbrev-ref", "HEAD").decode("utf-8", errors="strict").strip()
    branch = "DETACHED" if branch_name == "HEAD" else branch_name
    if not branch:
        raise SnapshotError("Git branch identity is empty")
    index_raw = _run_git(root, "ls-files", "--stage", "-z")
    status_raw = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    return {
        "head": head,
        "branch": branch,
        "index_tree": _digest(index_raw, "snapshot_entry"),
        "worktree_status_digest": _digest(status_raw, "snapshot_entry"),
    }


def _reject_path_argument_traversal(path_arg: str, *, label: str) -> None:
    candidate = path_arg.replace("\\", "/")
    if any(piece in {".", ".."} for piece in candidate.split("/")):
        raise SnapshotError(f"{label} must not contain '.' or '..' path components")


def _real_directory(path_arg: str, *, label: str) -> Path:
    """Return an absolute directory after lstat-checking every raw component."""
    _reject_path_argument_traversal(path_arg, label=label)
    absolute = Path(os.path.abspath(path_arg))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        try:
            info = os.lstat(current)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise SnapshotError(f"{label} does not exist: {path_arg}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SnapshotError(f"{label} must not traverse a symlink: {path_arg}")
    try:
        info = os.lstat(absolute)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise SnapshotError(f"{label} does not exist: {path_arg}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise SnapshotError(f"{label} must be a directory: {path_arg}")
    return absolute


def _require_descriptor_traversal() -> None:
    """Require the POSIX APIs needed to pin every path component by fd."""
    required_dir_fd = (os.open, os.stat, os.readlink)
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    supports_fd = getattr(os, "supports_fd", set())
    if (
        os.name != "posix"
        or any(function not in supports_dir_fd for function in required_dir_fd)
        or os.listdir not in supports_fd
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
        raise SnapshotError("descriptor-relative snapshot operations require POSIX dir_fd support")


def _directory_flags() -> int:
    _require_descriptor_traversal()
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _relative_parts(relative: str, *, max_depth: int | None = None) -> list[str]:
    pieces = relative.split("/")
    if not pieces or any(not piece or piece in {".", ".."} or "\\" in piece or "\x00" in piece for piece in pieces):
        raise SnapshotError(f"invalid repository-relative path: {relative!r}")
    depth_limit = SNAPSHOT_DEPTH if max_depth is None else max_depth
    if len(pieces) > depth_limit:
        raise SnapshotError(f"path exceeds {depth_limit} components: {relative!r}")
    return pieces


def _same_stat(first: os.stat_result, second: os.stat_result) -> bool:
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    return all(getattr(first, field) == getattr(second, field) for field in fields)


def _open_root_fd(root: Path) -> int:
    """Open the repository root without following any component."""
    flags = _directory_flags()
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in root.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise SnapshotError(f"cannot securely open repository root {root}: {exc}") from exc


def _open_relative_parent(root: Path, relative: str) -> tuple[int, str] | None:
    """Open the parent directory and return its basename, or None if absent."""
    pieces = _relative_parts(relative)
    descriptor = _open_root_fd(root)
    try:
        for component in pieces[:-1]:
            try:
                next_descriptor = os.open(component, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                os.close(descriptor)
                return None
            except OSError as exc:
                raise SnapshotError(f"cannot securely traverse {relative}: {exc}") from exc
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, pieces[-1]
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _entry_paths(root: Path, relative: str) -> Iterable[str]:
    opened = _open_relative_parent(root, relative)
    if opened is None:
        yield relative
        return
    parent_descriptor, name = opened
    try:
        try:
            info = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            yield relative
            return
        if stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode):
            yield relative
            return
        if not stat.S_ISDIR(info.st_mode):
            raise SnapshotError(f"unsupported special file in scope: {relative}")
        yield relative
        try:
            directory_descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
        except OSError as exc:
            raise SnapshotError(f"cannot open scoped directory {relative}: {exc}") from exc
        try:
            before = os.fstat(directory_descriptor)
            children = os.listdir(directory_descriptor)
            after = os.fstat(directory_descriptor)
            if not _same_stat(before, after):
                raise SnapshotError(f"scoped directory changed while listing: {relative}")
        finally:
            os.close(directory_descriptor)
        for child_name in sorted(children):
            if "\\" in child_name or "\x00" in child_name:
                raise SnapshotError(f"scoped filename cannot be represented safely: {relative}/{child_name}")
            child_relative = f"{relative}/{child_name}"
            yield from _entry_paths(root, child_relative)
        reopened = _open_relative_parent(root, relative)
        if reopened is None:
            raise SnapshotError(f"scoped directory disappeared while traversing: {relative}")
        reopened_parent, reopened_name = reopened
        try:
            try:
                final_info = os.stat(reopened_name, dir_fd=reopened_parent, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise SnapshotError(f"scoped directory disappeared while traversing: {relative}") from exc
            if not stat.S_ISDIR(final_info.st_mode) or not _same_stat(info, final_info):
                raise SnapshotError(f"scoped directory changed while traversing: {relative}")
        finally:
            os.close(reopened_parent)
    finally:
        os.close(parent_descriptor)


def _read_regular_file(parent_descriptor: int, name: str, before: os.stat_result, relative: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SnapshotError(f"cannot open scoped file {relative}: {exc}") from exc
    try:
        after_open = os.fstat(descriptor)
        if not stat.S_ISREG(after_open.st_mode) or not _same_stat(before, after_open):
            raise SnapshotError(f"scoped file changed type while reading: {relative}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        try:
            after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise SnapshotError(f"scoped file was removed while reading: {relative}") from exc
        if not _same_stat(before, after) or len(data) != before.st_size:
            raise SnapshotError(f"scoped file changed while reading: {relative}")
        return data
    finally:
        os.close(descriptor)


def _read_regular_relative(root: Path, relative: str) -> bytes:
    opened = _open_relative_parent(root, relative)
    if opened is None:
        raise SnapshotError(f"scoped file disappeared: {relative}")
    parent_descriptor, name = opened
    try:
        try:
            before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise SnapshotError(f"scoped file disappeared: {relative}") from exc
        if not stat.S_ISREG(before.st_mode):
            raise SnapshotError(f"scoped path is no longer a regular file: {relative}")
        return _read_regular_file(parent_descriptor, name, before, relative)
    finally:
        os.close(parent_descriptor)


def _deleted_entry(relative: str) -> dict[str, Any]:
    return {
        "path": relative,
        "status": "deleted",
        "type": "deleted",
        "mode": None,
        "size": None,
        "content_hash": None,
        "symlink_target": None,
    }


def _validate_symlink_target(target: Any, relative: str) -> str:
    limit = SYMLINK_TARGET_BYTES
    if not isinstance(target, str) or "\x00" in target:
        raise SnapshotError(f"symlink target is invalid: {relative}")
    try:
        size = len(target.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SnapshotError(f"symlink target is not valid UTF-8: {relative}") from exc
    if size > limit:
        raise SnapshotError(f"symlink target exceeds {limit} UTF-8 bytes: {relative}")
    return target


def _describe_entry(root: Path, relative: str) -> dict[str, Any]:
    opened = _open_relative_parent(root, relative)
    if opened is None:
        return _deleted_entry(relative)
    parent_descriptor, name = opened
    try:
        try:
            info = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return _deleted_entry(relative)
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            target = _validate_symlink_target(os.readlink(name, dir_fd=parent_descriptor), relative)
            try:
                after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise SnapshotError(f"scoped symlink was removed while reading: {relative}") from exc
            if not _same_stat(info, after):
                raise SnapshotError(f"scoped symlink changed while reading: {relative}")
            return {
                "path": relative,
                "status": "present",
                "type": "symlink",
                "mode": mode,
                "size": info.st_size,
                "content_hash": _digest(target.encode("utf-8"), "snapshot_entry"),
                "symlink_target": target,
            }
        if stat.S_ISDIR(info.st_mode):
            return {
                "path": relative,
                "status": "present",
                "type": "directory",
                "mode": mode,
                "size": None,
                "content_hash": None,
                "symlink_target": None,
            }
        if stat.S_ISREG(info.st_mode):
            data = _read_regular_file(parent_descriptor, name, info, relative)
            return {
                "path": relative,
                "status": "present",
                "type": "file",
                "mode": mode,
                "size": len(data),
                "content_hash": _digest(data, "snapshot_entry"),
                "symlink_target": None,
            }
        raise SnapshotError(f"unsupported special file in scope: {relative}")
    finally:
        os.close(parent_descriptor)


def _scope_paths(scope_path: str) -> tuple[dict[str, Any], list[str], str]:
    scope = load_json_file(scope_path)
    errors = validate_record(scope, "impact_scope")
    if errors:
        raise SnapshotError(f"invalid impact scope: {'; '.join(errors)}")
    canonical_scope = canonicalize_record(scope, "impact_scope")
    return canonical_scope, canonical_scope["changed_paths"], digest_record(scope, "impact_scope")


def _baseline_entries(root: Path, scope_paths: list[str], head: str) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    if head == "UNCOMMITTED":
        return [], {}
    raw = _run_git(root, "ls-tree", "-r", "-z", head, "--", *scope_paths)
    entries: list[dict[str, Any]] = []
    data_by_path: dict[str, bytes] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode_text, entry_type, object_id = metadata.decode("ascii").split(" ", 2)
            raw_relative = raw_path.decode("utf-8")
            if "\\" in raw_relative or "\x00" in raw_relative:
                raise SnapshotError("Git baseline path contains an unsupported character")
            relative = normalize_repo_path(raw_relative)
        except (UnicodeDecodeError, ValueError) as exc:
            raise SnapshotError("Git baseline tree contains an invalid path record") from exc
        if "\\" in relative or not any(_scope_covers(scope_path, relative) for scope_path in scope_paths):
            raise SnapshotError(f"Git baseline path is outside the declared scope: {relative}")
        if entry_type != "blob":
            raise SnapshotError(f"unsupported Git baseline entry type: {relative}")
        data = _run_git(root, "cat-file", "blob", object_id)
        if mode_text == "120000":
            target = _validate_symlink_target(data.decode("utf-8"), f"baseline entry {relative}")
            entry = {
                "path": relative,
                "status": "present",
                "type": "symlink",
                "mode": 0o777,
                "size": len(data),
                "content_hash": _digest(target.encode("utf-8"), "snapshot_entry"),
                "symlink_target": target,
                "artifact_path": None,
            }
        else:
            try:
                mode = int(mode_text, 8) & 0o7777
            except ValueError as exc:
                raise SnapshotError(f"Git baseline mode is invalid: {relative}") from exc
            entry = {
                "path": relative,
                "status": "present",
                "type": "file",
                "mode": mode,
                "size": len(data),
                "content_hash": _digest(data, "snapshot_entry"),
                "symlink_target": None,
                "artifact_path": f"{ARTIFACT_PREFIXES['baseline']}/{relative}",
            }
            data_by_path[relative] = data
        entries.append(entry)
    entries.sort(key=lambda entry: entry["path"])
    return entries, data_by_path


def _identity_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": manifest["version"],
        "base_git_identity": manifest["base_git_identity"],
        "scope_paths": manifest["scope_paths"],
        "scope_digest": manifest["scope_digest"],
        "entries": manifest["entries"],
        "baseline_entries": manifest["baseline_entries"],
    }


def _content_identity(manifest: dict[str, Any]) -> str:
    return _digest(canonical_json(_identity_payload(manifest)), "snapshot_content")


def _manifest_identity(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "manifest_identity"}
    return _digest(canonical_json(payload), "snapshot_manifest")


def _scope_covers(scope_path: str, path: str) -> bool:
    return path == scope_path or path.startswith(f"{scope_path}/")


def _validate_entry(
    entry: Any,
    *,
    index: int,
    label: str,
    artifact_prefix: str,
    allow_deleted: bool,
) -> str:
    if not isinstance(entry, dict):
        raise SnapshotError(f"{label}[{index}] must be an object")
    if set(entry) != ENTRY_FIELDS:
        raise SnapshotError(f"{label}[{index}] has an invalid closed shape")
    path = normalize_repo_path(entry["path"])
    if path != entry["path"]:
        raise SnapshotError(f"{label}[{index}].path is not normalized")
    if not isinstance(entry["status"], str) or entry["status"] not in ENTRY_STATUSES:
        raise SnapshotError(f"{label}[{index}].status is invalid")
    if not isinstance(entry["type"], str) or entry["type"] not in ENTRY_TYPES:
        raise SnapshotError(f"{label}[{index}].type is invalid")
    if entry["status"] == "deleted":
        if not allow_deleted or any(entry[key] is not None for key in ("mode", "size", "content_hash", "symlink_target", "artifact_path")) or entry["type"] != "deleted":
            raise SnapshotError(f"{label}[{index}] deleted shape is invalid")
        return path
    if entry["type"] == "deleted":
        raise SnapshotError(f"{label}[{index}] present entry cannot have type=deleted")
    if not isinstance(entry["mode"], int) or isinstance(entry["mode"], bool) or entry["mode"] < 0 or entry["mode"] > 0o7777:
        raise SnapshotError(f"{label}[{index}].mode is invalid")
    if entry["type"] == "file":
        if not isinstance(entry["size"], int) or entry["size"] < 0 or entry["artifact_path"] != f"{artifact_prefix}/{path}":
            raise SnapshotError(f"{label}[{index}] file size/artifact_path is invalid")
        _relative_parts(entry["artifact_path"], max_depth=SNAPSHOT_DEPTH + 1)
        _ensure_hash(entry["content_hash"], f"{label}[{index}].content_hash")
        if entry["symlink_target"] is not None:
            raise SnapshotError(f"{label}[{index}] file cannot have a symlink target")
    elif entry["type"] == "symlink":
        if not isinstance(entry["size"], int) or entry["size"] < 0 or entry["artifact_path"] is not None:
            raise SnapshotError(f"{label}[{index}] symlink size/artifact_path is invalid")
        target = _validate_symlink_target(entry["symlink_target"], f"{label}[{index}]")
        if entry["size"] != len(target.encode("utf-8")):
            raise SnapshotError(f"{label}[{index}] symlink size does not match its target")
        _ensure_hash(entry["content_hash"], f"{label}[{index}].content_hash")
        if _digest(entry["symlink_target"].encode("utf-8"), "snapshot_entry") != entry["content_hash"]:
            raise SnapshotError(f"{label}[{index}] symlink content_hash does not match its target")
    elif entry["type"] == "directory":
        if entry["size"] is not None or entry["content_hash"] is not None or entry["symlink_target"] is not None or entry["artifact_path"] is not None:
            raise SnapshotError(f"{label}[{index}] directory shape is invalid")
    return path


def _validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise SnapshotError("manifest must be a JSON object")
    if set(manifest) != MANIFEST_FIELDS:
        unknown = sorted(set(manifest) - MANIFEST_FIELDS)
        missing = sorted(MANIFEST_FIELDS - set(manifest))
        raise SnapshotError(f"manifest fields mismatch (missing={missing}, unknown={unknown})")
    if manifest["version"] != MANIFEST_VERSION:
        raise SnapshotError(f"manifest.version must be {MANIFEST_VERSION}")
    base = manifest["base_git_identity"]
    if not isinstance(base, dict) or set(base) != GIT_IDENTITY_FIELDS:
        raise SnapshotError("base_git_identity has an invalid closed shape")
    head = base["head"]
    if head != "UNCOMMITTED" and (
        not isinstance(head, str) or len(head) not in GIT_HEAD_LENGTHS or set(head) - HEX64
    ):
        raise SnapshotError("base_git_identity.head must be a Git object ID or UNCOMMITTED")
    if not isinstance(base["branch"], str) or not base["branch"] or base["branch"] in {"NONE", "UNKNOWN"} or "\x00" in base["branch"]:
        raise SnapshotError("base_git_identity.branch is invalid")
    _ensure_hash(base["index_tree"], "base_git_identity.index_tree")
    _ensure_hash(base["worktree_status_digest"], "base_git_identity.worktree_status_digest")
    if not isinstance(manifest["scope_paths"], list):
        raise SnapshotError("scope_paths must be a list")
    normalized_scope = [normalize_repo_path(item) for item in manifest["scope_paths"]]
    if normalized_scope != manifest["scope_paths"] or normalized_scope != sorted(set(normalized_scope)):
        raise SnapshotError("scope_paths must be normalized, unique, and sorted")
    _ensure_hash(manifest["scope_digest"], "scope_digest")
    entries = manifest["entries"]
    if not isinstance(entries, list):
        raise SnapshotError("entries must be a list")
    if len(entries) > MAX_ENTRIES:
        raise SnapshotError("entries exceed the bounded snapshot size")
    paths = [
        _validate_entry(entry, index=index, label="entries", artifact_prefix=ARTIFACT_PREFIXES["files"], allow_deleted=True)
        for index, entry in enumerate(entries)
    ]
    if len(paths) != len(set(paths)) or paths != sorted(paths):
        raise SnapshotError("entries must have unique paths in sorted order")
    baseline_entries = manifest["baseline_entries"]
    if not isinstance(baseline_entries, list):
        raise SnapshotError("baseline_entries must be a list")
    if len(baseline_entries) > MAX_ENTRIES:
        raise SnapshotError("baseline_entries exceed the bounded snapshot size")
    baseline_paths = [
        _validate_entry(entry, index=index, label="baseline_entries", artifact_prefix=ARTIFACT_PREFIXES["baseline"], allow_deleted=False)
        for index, entry in enumerate(baseline_entries)
    ]
    if any(entry["type"] not in BASELINE_TYPES for entry in baseline_entries):
        raise SnapshotError("baseline_entries may contain only files and symlinks")
    if len(baseline_paths) != len(set(baseline_paths)) or baseline_paths != sorted(baseline_paths):
        raise SnapshotError("baseline_entries must have unique paths in sorted order")
    entry_paths = set(paths)
    for scope_path in manifest["scope_paths"]:
        if scope_path not in entry_paths:
            raise SnapshotError(f"scope path is not represented in entries: {scope_path}")
    for path in entry_paths | set(baseline_paths):
        if not any(_scope_covers(scope_path, path) for scope_path in manifest["scope_paths"]):
            raise SnapshotError(f"entry is outside the declared scope: {path}")
    _ensure_hash(manifest["content_identity"], "content_identity")
    _ensure_hash(manifest["manifest_identity"], "manifest_identity")
    if _content_identity(manifest) != manifest["content_identity"]:
        raise SnapshotError("content_identity does not match the manifest payload")
    if _manifest_identity(manifest) != manifest["manifest_identity"]:
        raise SnapshotError("manifest_identity does not match the manifest")
    return manifest


def _open_absolute_parent(path: Path) -> tuple[int, str]:
    """Open an absolute path's parent without following any component."""
    if not path.is_absolute() or len(path.parts) < 2:
        raise SnapshotError("artifact output must name a non-root absolute path")
    descriptor = os.open(os.path.sep, _directory_flags())
    try:
        for component in path.parts[1:-1]:
            try:
                next_descriptor = os.open(component, _directory_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise SnapshotError(f"cannot securely open artifact output parent {path}: {exc}") from exc
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, path.parts[-1]
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _open_absolute_directory(path: Path) -> int:
    parent_descriptor, name = _open_absolute_parent(path)
    try:
        try:
            return os.open(name, _directory_flags(), dir_fd=parent_descriptor)
        except OSError as exc:
            raise SnapshotError(f"path must be a real directory: {path}") from exc
    finally:
        os.close(parent_descriptor)


def _read_open_descriptor(descriptor: int, label: str) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise SnapshotError(f"{label} is not a regular file")
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    after = os.fstat(descriptor)
    data = b"".join(chunks)
    if not _same_stat(before, after) or len(data) != before.st_size:
        raise SnapshotError(f"{label} changed while reading")
    return data


def _open_relative_parent_from_fd(base_descriptor: int, relative: str) -> tuple[int, str, list[int]]:
    pieces = _relative_parts(relative, max_depth=SNAPSHOT_DEPTH + 1)
    current = base_descriptor
    opened: list[int] = []
    try:
        for component in pieces[:-1]:
            current = os.open(component, _directory_flags(), dir_fd=current)
            opened.append(current)
        return current, pieces[-1], opened
    except OSError as exc:
        for descriptor in reversed(opened):
            os.close(descriptor)
        raise SnapshotError(f"cannot securely traverse artifact path {relative}: {exc}") from exc


def _read_artifact_file(artifact_descriptor: int, relative: str) -> bytes:
    parent_descriptor, name, opened = _open_relative_parent_from_fd(artifact_descriptor, relative)
    try:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_descriptor)
        except OSError as exc:
            raise SnapshotError(f"artifact file is not safely readable: {relative}") from exc
        try:
            return _read_open_descriptor(descriptor, f"artifact file {relative}")
        finally:
            os.close(descriptor)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _directory_listing(descriptor: int, label: str) -> list[str]:
    before = os.fstat(descriptor)
    names = os.listdir(descriptor)
    after = os.fstat(descriptor)
    if not _same_stat(before, after):
        raise SnapshotError(f"{label} changed while listing")
    return names


def _assert_private_stat(info: os.stat_result, *, directory: bool, label: str) -> None:
    mode = stat.S_IMODE(info.st_mode)
    required = 0o500 if directory else 0o400
    if mode != required:
        raise SnapshotError(f"{label} must have exact owner-only mode {oct(required)}")


def _collect_artifact_tree(descriptor: int, relative: str) -> tuple[set[str], set[str]]:
    if relative.count("/") + 1 > SNAPSHOT_DEPTH + 1:
        raise SnapshotError(f"artifact path exceeds {SNAPSHOT_DEPTH + 1} components")
    actual_files: set[str] = set()
    actual_directories: set[str] = {relative}
    for name in sorted(_directory_listing(descriptor, f"artifact directory {relative}")):
        if "\\" in name or "\x00" in name:
            raise SnapshotError(f"artifact name cannot be represented safely: {relative}/{name}")
        try:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise SnapshotError(f"artifact entry disappeared: {relative}/{name}") from exc
        child_relative = f"{relative}/{name}"
        if stat.S_ISLNK(info.st_mode):
            raise SnapshotError(f"artifact contains a symlink: {child_relative}")
        if stat.S_ISDIR(info.st_mode):
            _assert_private_stat(info, directory=True, label="artifact directory")
            try:
                child_descriptor = os.open(name, _directory_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise SnapshotError(f"cannot open artifact directory: {child_relative}") from exc
            try:
                nested_files, nested_directories = _collect_artifact_tree(child_descriptor, child_relative)
            finally:
                os.close(child_descriptor)
            actual_files.update(nested_files)
            actual_directories.update(nested_directories)
        elif stat.S_ISREG(info.st_mode):
            _assert_private_stat(info, directory=False, label="artifact file")
            actual_files.add(child_relative)
        else:
            raise SnapshotError(f"artifact contains an unsupported special file: {child_relative}")
    return actual_files, actual_directories


def _open_or_create_output(path: Path) -> int:
    parent_descriptor, name = _open_absolute_parent(path)
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            raise SnapshotError("artifact output must not already exist")
        try:
            descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
        except OSError as exc:
            raise SnapshotError(f"artifact output must be a real directory: {path}") from exc
        try:
            return descriptor
        except Exception:
            os.close(descriptor)
            raise
    finally:
        os.close(parent_descriptor)


def _open_or_create_output_directory(parent_descriptor: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_descriptor)
    except FileExistsError:
        pass
    try:
        return os.open(name, _directory_flags(), dir_fd=parent_descriptor)
    except OSError as exc:
        raise SnapshotError(f"artifact path component is not a real directory: {name}") from exc


def _write_output_file(output_descriptor: int, artifact_path: str, data: bytes) -> None:
    pieces = _relative_parts(artifact_path, max_depth=SNAPSHOT_DEPTH + 1)
    descriptors: list[int] = [output_descriptor]
    current = output_descriptor
    try:
        for component in pieces[:-1]:
            current = _open_or_create_output_directory(current, component)
            descriptors.append(current)
        try:
            descriptor = os.open(
                pieces[-1],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
                dir_fd=current,
            )
        except OSError as exc:
            raise SnapshotError(f"cannot create artifact file {artifact_path}: {exc}") from exc
        try:
            offset = 0
            while offset < len(data):
                written = os.write(descriptor, data[offset:])
                if written <= 0:
                    raise SnapshotError(f"cannot write artifact file {artifact_path}")
                offset += written
            os.fchmod(descriptor, 0o400)
        finally:
            os.close(descriptor)
    finally:
        for descriptor in reversed(descriptors[1:]):
            os.close(descriptor)


def _set_output_directory_modes(output_descriptor: int, directories: set[str]) -> None:
    for relative in sorted(directories, key=lambda item: (item.count("/"), item)):
        pieces = _relative_parts(relative, max_depth=SNAPSHOT_DEPTH + 1)
        descriptors: list[int] = []
        current = output_descriptor
        try:
            for component in pieces:
                current = os.open(component, _directory_flags(), dir_fd=current)
                descriptors.append(current)
            os.fchmod(current, 0o500)
        except OSError as exc:
            raise SnapshotError(f"cannot make artifact directory read-only: {relative}") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def create_snapshot(root_arg: str, scope_arg: str, output_arg: str) -> dict[str, Any]:
    root = _real_directory(root_arg, label="repository root")
    _reject_path_argument_traversal(output_arg, label="artifact output")
    output = Path(os.path.abspath(output_arg))
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise SnapshotError("artifact output must be outside the repository root")
    _, scope_paths, scope_digest = _scope_paths(scope_arg)
    all_paths: set[str] = set()
    for scope_path in scope_paths:
        all_paths.update(_entry_paths(root, scope_path))
    entries: list[dict[str, Any]] = []
    for relative in sorted(all_paths):
        described = _describe_entry(root, relative)
        described["artifact_path"] = f"{ARTIFACT_PREFIXES['files']}/{relative}" if described["type"] == "file" else None
        entries.append(described)
    base_git_identity = git_identity(root)
    baseline_entries, baseline_data = _baseline_entries(root, scope_paths, base_git_identity["head"])
    if len(entries) > MAX_ENTRIES or len(baseline_entries) > MAX_ENTRIES:
        raise SnapshotError("snapshot entries exceed the bounded artifact size")
    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "base_git_identity": base_git_identity,
        "scope_paths": scope_paths,
        "scope_digest": scope_digest,
        "entries": entries,
        "baseline_entries": baseline_entries,
    }
    manifest["content_identity"] = _content_identity(manifest)
    manifest["manifest_identity"] = _manifest_identity(manifest)
    output_descriptor = _open_or_create_output(output)
    try:
        directories = set(ARTIFACT_PREFIXES.values())
        for directory in ARTIFACT_PREFIXES.values():
            directory_descriptor = _open_or_create_output_directory(output_descriptor, directory)
            os.close(directory_descriptor)
        for entry in entries:
            if entry["type"] != "file":
                continue
            artifact_path = entry["artifact_path"]
            artifact_pieces = _relative_parts(artifact_path, max_depth=SNAPSHOT_DEPTH + 1)
            for length in range(1, len(artifact_pieces)):
                directories.add("/".join(artifact_pieces[:length]))
            data = _read_regular_relative(root, entry["path"])
            if _digest(data, "snapshot_entry") != entry["content_hash"]:
                raise SnapshotError(f"source changed during snapshot: {entry['path']}")
            _write_output_file(output_descriptor, artifact_path, data)
        for entry in baseline_entries:
            if entry["type"] != "file":
                continue
            data = baseline_data[entry["path"]]
            if _digest(data, "snapshot_entry") != entry["content_hash"]:
                raise SnapshotError(f"Git baseline changed during snapshot: {entry['path']}")
            artifact_path = entry["artifact_path"]
            artifact_pieces = _relative_parts(artifact_path, max_depth=SNAPSHOT_DEPTH + 1)
            for length in range(1, len(artifact_pieces)):
                directories.add("/".join(artifact_pieces[:length]))
            _write_output_file(output_descriptor, artifact_path, data)
        current_git_identity = git_identity(root)
        if current_git_identity != base_git_identity:
            raise SnapshotError("Git identity changed during snapshot")
        current_paths: set[str] = set()
        for scope_path in scope_paths:
            current_paths.update(_entry_paths(root, scope_path))
        current_entries: list[dict[str, Any]] = []
        for relative in sorted(current_paths):
            current_entry = _describe_entry(root, relative)
            current_entry["artifact_path"] = f"{ARTIFACT_PREFIXES['files']}/{relative}" if current_entry["type"] == "file" else None
            current_entries.append(current_entry)
        if current_entries != entries:
            raise SnapshotError("source workspace changed during snapshot")
        _set_output_directory_modes(output_descriptor, directories)
        manifest_bytes = canonical_json(manifest) + b"\n"
        if len(manifest_bytes) > MANIFEST_BYTES:
            raise SnapshotError("snapshot manifest exceeds the bounded JSON size")
        _write_output_file(output_descriptor, "manifest.json", manifest_bytes)
        os.fchmod(output_descriptor, 0o500)
    finally:
        os.close(output_descriptor)
    return {
        "artifact": str(output),
        "content_identity": manifest["content_identity"],
        "manifest_identity": manifest["manifest_identity"],
        "scope_digest": scope_digest,
    }


def _load_manifest(artifact_arg: str) -> tuple[Path, dict[str, Any]]:
    _reject_path_argument_traversal(artifact_arg, label="artifact path")
    artifact = Path(os.path.abspath(artifact_arg))
    artifact_descriptor = _open_absolute_directory(artifact)
    try:
        manifest = _read_manifest_from_descriptor(artifact_descriptor, artifact)
    finally:
        os.close(artifact_descriptor)
    return artifact, manifest


def _read_manifest_from_descriptor(artifact_descriptor: int, artifact: Path) -> dict[str, Any]:
    try:
        manifest_descriptor = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=artifact_descriptor)
    except OSError as exc:
        raise SnapshotError("artifact manifest is missing or is not safely readable") from exc
    try:
        manifest_bytes = _read_open_descriptor(manifest_descriptor, "artifact manifest")
    finally:
        os.close(manifest_descriptor)
    try:
        manifest = load_json_bytes(manifest_bytes, source=str(artifact / "manifest.json"))
    except ContractError as exc:
        raise SnapshotError(str(exc)) from exc
    return _validate_manifest(manifest)


def _expected_scope(scope_arg: str | None) -> tuple[list[str], str] | None:
    if scope_arg is None:
        return None
    _, scope_paths, scope_digest = _scope_paths(scope_arg)
    return scope_paths, scope_digest


def verify_artifact(artifact_arg: str, scope_arg: str | None = None) -> dict[str, Any]:
    expected_scope = _expected_scope(scope_arg)
    artifact, manifest = _load_manifest(artifact_arg)
    artifact_descriptor = _open_absolute_directory(artifact)
    try:
        current_manifest = _read_manifest_from_descriptor(artifact_descriptor, artifact)
        if current_manifest != manifest:
            raise SnapshotError("artifact manifest changed during verification")
        if expected_scope is not None:
            expected_paths, expected_digest = expected_scope
            if manifest["scope_paths"] != expected_paths or manifest["scope_digest"] != expected_digest:
                raise SnapshotError("artifact scope does not match the supplied canonical scope")
        _assert_private_stat(os.fstat(artifact_descriptor), directory=True, label="artifact directory")
        top_level = set(_directory_listing(artifact_descriptor, "artifact root"))
        if top_level != TOP_LEVEL:
            raise SnapshotError(f"artifact top-level set mismatch: {sorted(top_level)}")
        for name, directory in (("manifest.json", False), *[(prefix, True) for prefix in ARTIFACT_PREFIXES.values()]):
            info = os.stat(name, dir_fd=artifact_descriptor, follow_symlinks=False)
            if (directory and not stat.S_ISDIR(info.st_mode)) or (not directory and not stat.S_ISREG(info.st_mode)):
                raise SnapshotError(f"artifact top-level entry has the wrong type: {name}")
            _assert_private_stat(info, directory=directory, label=f"artifact top-level entry: {name}")
        all_entries = manifest["entries"] + manifest["baseline_entries"]
        expected_files = {entry["artifact_path"] for entry in all_entries if entry["type"] == "file"}
        expected_directories = set(ARTIFACT_PREFIXES.values())
        for path in expected_files:
            parent = Path(path).parent
            while str(parent) != ".":
                expected_directories.add(parent.as_posix())
                parent = parent.parent
        actual_files: set[str] = set()
        actual_directories: set[str] = set()
        for root_name in ARTIFACT_PREFIXES.values():
            files_descriptor = os.open(root_name, _directory_flags(), dir_fd=artifact_descriptor)
            try:
                tree_files, tree_directories = _collect_artifact_tree(files_descriptor, root_name)
            finally:
                os.close(files_descriptor)
            actual_files.update(tree_files)
            actual_directories.update(tree_directories)
        if actual_files != expected_files:
            raise SnapshotError(f"artifact file set mismatch (missing={sorted(expected_files - actual_files)}, extra={sorted(actual_files - expected_files)})")
        if actual_directories != expected_directories:
            raise SnapshotError(f"artifact directory set mismatch (missing={sorted(expected_directories - actual_directories)}, extra={sorted(actual_directories - expected_directories)})")
        for entry in all_entries:
            if entry["type"] != "file":
                continue
            data = _read_artifact_file(artifact_descriptor, entry["artifact_path"])
            if len(data) != entry["size"] or _digest(data, "snapshot_entry") != entry["content_hash"]:
                raise SnapshotError(f"artifact content mismatch: {entry['path']}")
        final_top_level = set(_directory_listing(artifact_descriptor, "artifact root"))
        if final_top_level != top_level:
            raise SnapshotError("artifact top-level set changed during verification")
        final_files: set[str] = set()
        final_directories: set[str] = set()
        for root_name in ARTIFACT_PREFIXES.values():
            files_descriptor = os.open(root_name, _directory_flags(), dir_fd=artifact_descriptor)
            try:
                tree_files, tree_directories = _collect_artifact_tree(files_descriptor, root_name)
            finally:
                os.close(files_descriptor)
            final_files.update(tree_files)
            final_directories.update(tree_directories)
        if final_files != actual_files or final_directories != actual_directories:
            raise SnapshotError("artifact file or directory set changed during verification")
        for entry in all_entries:
            if entry["type"] != "file":
                continue
            data = _read_artifact_file(artifact_descriptor, entry["artifact_path"])
            if len(data) != entry["size"] or _digest(data, "snapshot_entry") != entry["content_hash"]:
                raise SnapshotError(f"artifact content changed during verification: {entry['path']}")
    finally:
        os.close(artifact_descriptor)
    return {
        "artifact": str(artifact),
        "valid": True,
        "content_identity": manifest["content_identity"],
        "manifest_identity": manifest["manifest_identity"],
    }


def compare_workspace(root_arg: str, artifact_arg: str, scope_arg: str | None = None) -> tuple[int, dict[str, Any]]:
    artifact_result = verify_artifact(artifact_arg, scope_arg)
    artifact, manifest = _load_manifest(artifact_arg)
    root = _real_directory(root_arg, label="repository root")
    actual_git_identity = git_identity(root)
    if actual_git_identity != manifest["base_git_identity"]:
        return 1, {
            "artifact": str(artifact),
            "match": False,
            "reason": "GIT_IDENTITY_MISMATCH",
            "expected": manifest["base_git_identity"],
            "actual": actual_git_identity,
        }
    expected_by_path = {entry["path"]: entry for entry in manifest["entries"]}
    current_paths: set[str] = set()
    for scope_path in manifest["scope_paths"]:
        current_paths.update(_entry_paths(root, scope_path))
    if current_paths != set(expected_by_path):
        return 1, {
            "artifact": str(artifact),
            "match": False,
            "reason": "WORKSPACE_PATH_SET_MISMATCH",
            "missing": sorted(set(expected_by_path) - current_paths),
            "extra": sorted(current_paths - set(expected_by_path)),
        }
    mismatches: list[str] = []
    current_entries: list[dict[str, Any]] = []
    for relative in sorted(current_paths):
        current_entry = _describe_entry(root, relative)
        current_entry["artifact_path"] = expected_by_path[relative]["artifact_path"]
        current_entries.append(current_entry)
        if current_entry != expected_by_path[relative]:
            mismatches.append(relative)
    if mismatches:
        return 1, {
            "artifact": str(artifact),
            "match": False,
            "reason": "WORKSPACE_CONTENT_MISMATCH",
            "paths": mismatches,
        }
    current_manifest = {
        "version": MANIFEST_VERSION,
        "base_git_identity": manifest["base_git_identity"],
        "scope_paths": manifest["scope_paths"],
        "scope_digest": manifest["scope_digest"],
        "entries": current_entries,
        "baseline_entries": manifest["baseline_entries"],
    }
    current_identity = _content_identity(current_manifest)
    if current_identity != manifest["content_identity"]:
        return 1, {
            "artifact": str(artifact),
            "match": False,
            "reason": "CONTENT_IDENTITY_MISMATCH",
            "expected": manifest["content_identity"],
            "actual": current_identity,
        }
    final_git_identity = git_identity(root)
    if final_git_identity != actual_git_identity:
        return 1, {
            "artifact": str(artifact),
            "match": False,
            "reason": "GIT_IDENTITY_CHANGED_DURING_COMPARE",
            "expected": actual_git_identity,
            "actual": final_git_identity,
        }
    result = dict(artifact_result)
    result.update({"match": True, "workspace": str(root)})
    return 0, result


def _print_error(error: Exception) -> int:
    print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=True, sort_keys=True))
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("root")
    create.add_argument("--scope", required=True, help="ContractV2 impact-scope JSON")
    create.add_argument("--output", required=True, help="new artifact directory outside root")
    verify = subparsers.add_parser("verify")
    verify.add_argument("artifact")
    verify.add_argument("--scope", help="expected ContractV2 impact-scope JSON")
    compare = subparsers.add_parser("compare")
    compare.add_argument("root")
    compare.add_argument("artifact")
    compare.add_argument("--scope", help="expected ContractV2 impact-scope JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_snapshot(args.root, args.scope, args.output)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            return 0
        if args.command == "verify":
            print(json.dumps(verify_artifact(args.artifact, args.scope), ensure_ascii=True, sort_keys=True))
            return 0
        code, result = compare_workspace(args.root, args.artifact, args.scope)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return code
    except (ContractError, SnapshotError, OSError, UnicodeError, TypeError, KeyError, ValueError, RecursionError) as exc:
        return _print_error(exc)


if __name__ == "__main__":
    sys.exit(main())
