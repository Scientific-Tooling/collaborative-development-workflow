#!/usr/bin/env python3
"""Create and verify frozen, scoped review artifacts for ContractV2."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - reported by the platform probe
    fcntl = None  # type: ignore[assignment]

_previous_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
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
finally:
    sys.dont_write_bytecode = _previous_dont_write_bytecode
    del _previous_dont_write_bytecode


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
MAX_FILE_BYTES = _artifact_limit("file_bytes_ref")
MAX_TOTAL_BYTES = _artifact_limit("total_bytes_ref")
MAX_GIT_OUTPUT_BYTES = _artifact_limit("git_output_bytes_ref")
GIT_TIMEOUT_SECONDS = _contract_limit("limits.git_timeout_seconds")
HEX64 = set("0123456789abcdef")
GIT_HEAD_LENGTHS = {40, 64}


class SnapshotError(ValueError):
    """An invalid snapshot input or artifact."""


class GitCommandError(SnapshotError):
    """A bounded Git query completed with a nonzero status."""


def _digest(data: bytes, domain: str) -> str:
    return hashlib.sha256(DOMAINS[domain] + data).hexdigest()


def _ensure_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - HEX64:
        raise SnapshotError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _descriptor_operations_supported() -> bool:
    required_dir_fd = (os.open, os.stat, os.readlink)
    return bool(
        os.name == "posix"
        and fcntl is not None
        and all(function in getattr(os, "supports_dir_fd", set()) for function in required_dir_fd)
        and os.scandir in getattr(os, "supports_fd", set())
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and hasattr(fcntl, "F_DUPFD_CLOEXEC")
    )


def _load_renameat2() -> Any | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        function = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    except OSError:
        return None
    if function is None:
        return None
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    return function


def _descriptor_path(descriptor: int) -> str:
    expected = os.fstat(descriptor)
    for prefix in ("/proc/self/fd", "/dev/fd"):
        candidate = f"{prefix}/{descriptor}"
        try:
            actual = os.stat(candidate)
        except OSError:
            continue
        if stat.S_ISDIR(actual.st_mode) and _same_inode(actual, expected):
            return candidate
    raise SnapshotError("descriptor-backed Git paths are unavailable on this platform")


def _atomic_noreplace_supported() -> bool:
    renameat2 = _load_renameat2()
    if renameat2 is None:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="cdw-rename-probe-") as temporary:
            parent = Path(temporary)
            (parent / "source").mkdir()
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                ctypes.set_errno(0)
                return renameat2(
                    descriptor,
                    b"source",
                    descriptor,
                    b"target",
                    1,  # RENAME_NOREPLACE
                ) == 0
            finally:
                os.close(descriptor)
    except OSError:
        return False


def platform_capabilities() -> dict[str, bool]:
    """Return the exact platform primitives required by snapshot operations."""
    descriptor_operations = _descriptor_operations_supported()
    descriptor_git_path = False
    if descriptor_operations:
        descriptor = -1
        try:
            descriptor = os.open(os.path.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if descriptor < 3:
                replacement = fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)
                os.close(descriptor)
                descriptor = replacement
            _descriptor_path(descriptor)
            descriptor_git_path = True
        except (OSError, SnapshotError):
            descriptor_git_path = False
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return {
        "descriptor_operations": descriptor_operations,
        "descriptor_git_path": descriptor_git_path,
        "atomic_noreplace_publication": _atomic_noreplace_supported(),
    }


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name != "posix":
            raise OSError("process groups are unavailable")
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        if process.poll() is None:
            process.kill()
    process.wait()


def _git_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
    }


def git_executable_probe() -> tuple[bool, str]:
    """Boundedly confirm that the Git executable can start and exit successfully."""
    try:
        process = subprocess.Popen(
            ["git", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        return False, f"cannot execute git: {exc}"
    try:
        returncode = process.wait(timeout=GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        return False, f"git --version exceeded {GIT_TIMEOUT_SECONDS} seconds"
    if returncode != 0:
        return False, f"git --version exited with status {returncode}"
    return True, "bounded sanitized git --version succeeded"


def _run_git(root_descriptor: int, *args: str, max_bytes: int | None = None) -> bytes:
    command = [
        "git",
        "--no-optional-locks",
        "--literal-pathspecs",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        f"--work-tree={_descriptor_path(root_descriptor)}",
        "-C",
        _descriptor_path(root_descriptor),
        *args,
    ]
    environment = _git_environment()
    limit = MAX_GIT_OUTPUT_BYTES if max_bytes is None else max_bytes
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            pass_fds=(root_descriptor,),
            start_new_session=True,
        )
    except OSError as exc:
        raise SnapshotError(f"cannot run Git identity query: {exc}") from exc
    assert process.stdout is not None
    chunks: list[bytes] = []
    size = 0
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SnapshotError(
                    f"Git query exceeded {GIT_TIMEOUT_SECONDS} seconds: {' '.join(args)}"
                )
            if not selector.select(remaining):
                if process.poll() is None:
                    raise SnapshotError(
                        f"Git query exceeded {GIT_TIMEOUT_SECONDS} seconds: {' '.join(args)}"
                    )
                continue
            chunk = os.read(process.stdout.fileno(), min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise SnapshotError(f"Git output exceeds {limit} bytes: {' '.join(args)}")
    except BaseException:
        _terminate_process_group(process)
        raise
    finally:
        selector.close()
        process.stdout.close()
    try:
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise SnapshotError(
            f"Git query exceeded {GIT_TIMEOUT_SECONDS} seconds: {' '.join(args)}"
        ) from exc
    if returncode != 0:
        raise GitCommandError(f"Git identity query failed: {' '.join(args)}")
    return b"".join(chunks)


def _worktree_status_inventory(root_descriptor: int) -> bytes:
    """Return a bounded worktree inventory without content conversion or filters."""
    deleted = _run_git(root_descriptor, "ls-files", "--deleted", "-z", "--")
    untracked = _run_git(
        root_descriptor,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
    )
    return b"".join(
        (
            b"deleted\0",
            len(deleted).to_bytes(8, "big"),
            deleted,
            b"untracked\0",
            len(untracked).to_bytes(8, "big"),
            untracked,
        )
    )


def _git_identity(root_descriptor: int) -> dict[str, str]:
    try:
        git_entry = os.stat(".git", dir_fd=root_descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise SnapshotError("repository root must be the Git worktree top level") from exc
    if not (stat.S_ISDIR(git_entry.st_mode) or stat.S_ISREG(git_entry.st_mode)):
        raise SnapshotError("repository root must contain a real Git metadata entry")
    inside = _run_git(root_descriptor, "rev-parse", "--is-inside-work-tree").decode("utf-8", errors="strict").strip()
    if inside != "true":
        raise SnapshotError("repository root is not a Git worktree")
    prefix = _run_git(root_descriptor, "rev-parse", "--show-prefix").decode("utf-8", errors="strict").strip()
    if prefix:
        raise SnapshotError("repository root must be the Git worktree top level")
    try:
        head = _run_git(root_descriptor, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    except GitCommandError as head_error:
        branch_ref = _run_git(root_descriptor, "symbolic-ref", "--quiet", "HEAD").decode(
            "utf-8", errors="strict"
        ).strip()
        branch_tip = _run_git(
            root_descriptor,
            "for-each-ref",
            "--format=%(objectname)",
            "--count=1",
            branch_ref,
        ).strip()
        if branch_tip:
            raise head_error
        head = "UNCOMMITTED"
        branch = branch_ref
    else:
        branch_ref = _run_git(
            root_descriptor, "rev-parse", "--symbolic-full-name", "HEAD"
        ).decode("utf-8", errors="strict").strip()
        branch = "DETACHED" if branch_ref == "HEAD" else branch_ref
    if not branch:
        raise SnapshotError("Git branch identity is empty")
    index_raw = _run_git(root_descriptor, "ls-files", "--stage", "-z")
    staged_raw = _run_git(
        root_descriptor,
        "diff",
        "--cached",
        "--raw",
        "--no-abbrev",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--ignore-submodules=none",
        f"-O{os.devnull}",
        "--ita-invisible-in-index",
        "-z",
        "--",
    )
    index_inventory = b"".join(
        (
            b"stage\0",
            len(index_raw).to_bytes(8, "big"),
            index_raw,
            b"staged-diff\0",
            len(staged_raw).to_bytes(8, "big"),
            staged_raw,
        )
    )
    status_raw = _worktree_status_inventory(root_descriptor)
    return {
        "head": head,
        "branch": branch,
        "index_tree": _digest(index_inventory, "snapshot_entry"),
        "worktree_status_digest": _digest(status_raw, "snapshot_entry"),
    }


def git_identity(root: Path) -> dict[str, str]:
    """Return identity for a path while binding every query to one root inode."""
    root = Path(os.path.abspath(root))
    descriptor = _open_root_fd(root)
    try:
        identity = _git_identity(descriptor)
        _assert_directory_path_binding(root, descriptor, "repository root")
        return identity
    finally:
        os.close(descriptor)


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
    if not _descriptor_operations_supported():
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
        if descriptor < 3:
            replacement = fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)
            os.close(descriptor)
            descriptor = replacement
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise SnapshotError(f"cannot securely open repository root {root}: {exc}") from exc


def _open_relative_parent(root_descriptor: int, relative: str) -> tuple[int, str] | None:
    """Open the parent directory and return its basename, or None if absent."""
    pieces = _relative_parts(relative)
    descriptor = os.dup(root_descriptor)
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


def _entry_paths(
    root_descriptor: int, relative: str, budget: list[int] | None = None
) -> Iterable[str]:
    if budget is None:
        budget = [MAX_ENTRIES]
    if budget[0] <= 0:
        raise SnapshotError("snapshot entries exceed the bounded artifact size")
    budget[0] -= 1
    opened = _open_relative_parent(root_descriptor, relative)
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
            if not _same_stat(info, before):
                raise SnapshotError(f"scoped directory changed before listing: {relative}")
            children = _directory_listing(
                directory_descriptor,
                f"scoped directory {relative}",
                max_entries=budget[0],
            )
            after = os.fstat(directory_descriptor)
            if not _same_stat(before, after):
                raise SnapshotError(f"scoped directory changed while listing: {relative}")
        finally:
            os.close(directory_descriptor)
        for child_name in sorted(children):
            if "\\" in child_name or "\x00" in child_name:
                raise SnapshotError(f"scoped filename cannot be represented safely: {relative}/{child_name}")
            child_relative = f"{relative}/{child_name}"
            yield from _entry_paths(root_descriptor, child_relative, budget)
        reopened = _open_relative_parent(root_descriptor, relative)
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


def _read_regular_file(
    parent_descriptor: int,
    name: str,
    before: os.stat_result,
    relative: str,
    *,
    max_bytes: int = MAX_FILE_BYTES,
) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SnapshotError(f"cannot open scoped file {relative}: {exc}") from exc
    try:
        after_open = os.fstat(descriptor)
        if not stat.S_ISREG(after_open.st_mode) or not _same_stat(before, after_open):
            raise SnapshotError(f"scoped file changed type while reading: {relative}")
        if before.st_size > max_bytes:
            raise SnapshotError(f"scoped file exceeds {max_bytes} bytes: {relative}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise SnapshotError(f"scoped file exceeds {max_bytes} bytes: {relative}")
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


def _read_regular_relative(root_descriptor: int, relative: str) -> bytes:
    opened = _open_relative_parent(root_descriptor, relative)
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


def _describe_entry(root_descriptor: int, relative: str) -> dict[str, Any]:
    opened = _open_relative_parent(root_descriptor, relative)
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
    return canonical_scope, canonical_scope["review_paths"], digest_record(scope, "impact_scope")


def _baseline_entries(
    root_descriptor: int, scope_paths: list[str], head: str
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    if head == "UNCOMMITTED" or not scope_paths:
        return [], {}
    raw = _run_git(root_descriptor, "ls-tree", "-r", "-z", head, "--", *scope_paths)
    parsed_records: list[tuple[str, str, str]] = []
    seen_paths: set[str] = set()
    for record in raw.split(b"\x00"):
        if not record:
            continue
        if len(parsed_records) >= MAX_ENTRIES:
            raise SnapshotError("Git baseline entries exceed the bounded snapshot size")
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
        if relative in seen_paths:
            raise SnapshotError(f"Git baseline contains a duplicate path: {relative}")
        seen_paths.add(relative)
        parsed_records.append((mode_text, object_id, relative))

    entries: list[dict[str, Any]] = []
    data_by_path: dict[str, bytes] = {}
    total_bytes = 0
    for mode_text, object_id, relative in parsed_records:
        data = _run_git(root_descriptor, "cat-file", "blob", object_id, max_bytes=MAX_FILE_BYTES)
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_BYTES:
            raise SnapshotError(f"Git baseline exceeds {MAX_TOTAL_BYTES} bytes")
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
    branch = base["branch"]
    if (
        not isinstance(branch, str)
        or not branch
        or branch in {"NONE", "UNKNOWN"}
        or "\x00" in branch
        or (branch != "DETACHED" and not branch.startswith("refs/"))
    ):
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
    file_entries = [
        entry for entry in entries + baseline_entries if entry["type"] == "file"
    ]
    if any(entry["size"] > MAX_FILE_BYTES for entry in file_entries):
        raise SnapshotError("artifact contains a file larger than the declared limit")
    if sum(entry["size"] for entry in file_entries) > MAX_TOTAL_BYTES:
        raise SnapshotError("artifact content exceeds the declared total byte limit")
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


def _assert_directory_path_binding(path: Path, descriptor: int, label: str) -> None:
    parent_descriptor, name = _open_absolute_parent(path)
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except (FileNotFoundError, OSError) as exc:
        os.close(parent_descriptor)
        raise SnapshotError(f"{label} path is no longer bound to the opened directory") from exc
    try:
        if not stat.S_ISDIR(current.st_mode) or not _same_inode(current, os.fstat(descriptor)):
            raise SnapshotError(f"{label} path was replaced during the operation")
    finally:
        os.close(parent_descriptor)


def _read_open_descriptor(descriptor: int, label: str, *, max_bytes: int) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise SnapshotError(f"{label} is not a regular file")
    if before.st_size > max_bytes:
        raise SnapshotError(f"{label} exceeds {max_bytes} bytes")
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            raise SnapshotError(f"{label} exceeds {max_bytes} bytes")
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
            return _read_open_descriptor(
                descriptor, f"artifact file {relative}", max_bytes=MAX_FILE_BYTES
            )
        finally:
            os.close(descriptor)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _directory_listing(
    descriptor: int, label: str, *, max_entries: int = MAX_ENTRIES
) -> list[str]:
    before = os.fstat(descriptor)
    names: list[str] = []
    try:
        with os.scandir(descriptor) as iterator:
            for entry in iterator:
                names.append(entry.name)
                if len(names) > max_entries:
                    raise SnapshotError(f"{label} exceeds the bounded entry count")
    except OSError as exc:
        raise SnapshotError(f"cannot list {label}: {exc}") from exc
    after = os.fstat(descriptor)
    if not _same_stat(before, after):
        raise SnapshotError(f"{label} changed while listing")
    return names


def _assert_private_stat(info: os.stat_result, *, directory: bool, label: str) -> None:
    mode = stat.S_IMODE(info.st_mode)
    required = 0o500 if directory else 0o400
    if mode != required:
        raise SnapshotError(f"{label} must have exact owner-only mode {oct(required)}")


def _collect_artifact_tree(
    descriptor: int, relative: str, budget: list[int] | None = None
) -> tuple[set[str], set[str]]:
    if budget is None:
        budget = [MAX_ENTRIES]
    if relative.count("/") + 1 > SNAPSHOT_DEPTH + 1:
        raise SnapshotError(f"artifact path exceeds {SNAPSHOT_DEPTH + 1} components")
    actual_files: set[str] = set()
    actual_directories: set[str] = {relative}
    for name in sorted(
        _directory_listing(
            descriptor,
            f"artifact directory {relative}",
            max_entries=budget[0],
        )
    ):
        if budget[0] <= 0:
            raise SnapshotError("artifact tree exceeds the bounded entry count")
        budget[0] -= 1
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
                nested_files, nested_directories = _collect_artifact_tree(
                    child_descriptor, child_relative, budget
                )
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


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _remove_tree_contents(descriptor: int) -> None:
    os.fchmod(descriptor, 0o700)
    for name in _directory_listing(
        descriptor, "owned staging directory", max_entries=MAX_ENTRIES * 2 + 8
    ):
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, _directory_flags(), dir_fd=descriptor)
            try:
                _remove_tree_contents(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=descriptor)
        else:
            os.unlink(name, dir_fd=descriptor)


def _remove_owned_directory(
    parent_descriptor: int, name: str, expected: os.stat_result
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(current.st_mode) or not _same_inode(current, expected):
        raise SnapshotError(f"refusing to remove a replaced artifact path: {name}")
    descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
    try:
        if not _same_inode(os.fstat(descriptor), expected):
            raise SnapshotError(f"refusing to remove a replaced artifact path: {name}")
        _remove_tree_contents(descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent_descriptor)


def _rename_noreplace(
    parent_descriptor: int, source_name: str, target_name: str
) -> None:
    renameat2 = _load_renameat2()
    if renameat2 is None:
        raise SnapshotError("atomic no-replace artifact publication is unavailable")
    if renameat2(
        parent_descriptor,
        os.fsencode(source_name),
        parent_descriptor,
        os.fsencode(target_name),
        1,  # RENAME_NOREPLACE
    ) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise SnapshotError("artifact output must not already exist")
    if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
        raise SnapshotError("atomic no-replace artifact publication is unavailable")
    raise SnapshotError(f"cannot publish artifact atomically: {os.strerror(error)}")


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
    root_descriptor = _open_root_fd(root)
    try:
        try:
            output.relative_to(root)
        except ValueError:
            pass
        else:
            raise SnapshotError("artifact output must be outside the repository root")
        _, scope_paths, scope_digest = _scope_paths(scope_arg)
        if _load_renameat2() is None:
            raise SnapshotError("atomic no-replace artifact publication is unavailable")
        base_git_identity = _git_identity(root_descriptor)
        all_paths: set[str] = set()
        entry_budget = [MAX_ENTRIES]
        for scope_path in scope_paths:
            all_paths.update(_entry_paths(root_descriptor, scope_path, entry_budget))
        entries: list[dict[str, Any]] = []
        for relative in sorted(all_paths):
            described = _describe_entry(root_descriptor, relative)
            described["artifact_path"] = f"{ARTIFACT_PREFIXES['files']}/{relative}" if described["type"] == "file" else None
            entries.append(described)
        baseline_entries, baseline_data = _baseline_entries(
            root_descriptor, scope_paths, base_git_identity["head"]
        )
        if len(entries) > MAX_ENTRIES or len(baseline_entries) > MAX_ENTRIES:
            raise SnapshotError("snapshot entries exceed the bounded artifact size")
        content_bytes = sum(
            entry["size"]
            for entry in entries + baseline_entries
            if entry["type"] == "file"
        )
        if content_bytes > MAX_TOTAL_BYTES:
            raise SnapshotError(f"snapshot content exceeds {MAX_TOTAL_BYTES} bytes")
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
        manifest_bytes = canonical_json(manifest) + b"\n"
        if len(manifest_bytes) > MANIFEST_BYTES:
            raise SnapshotError("snapshot manifest exceeds the bounded JSON size")
        if content_bytes + len(manifest_bytes) > MAX_TOTAL_BYTES:
            raise SnapshotError(f"snapshot artifact exceeds {MAX_TOTAL_BYTES} bytes")

        output_parent, output_name = _open_absolute_parent(output)
        stage_name = f".{output_name}.cdw-stage-{secrets.token_hex(16)}"
        stage_descriptor: int | None = None
        stage_stat: os.stat_result | None = None
        published = False
        try:
            try:
                os.stat(output_name, dir_fd=output_parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SnapshotError("artifact output must not already exist")
            os.mkdir(stage_name, 0o700, dir_fd=output_parent)
            stage_descriptor = os.open(stage_name, _directory_flags(), dir_fd=output_parent)
            stage_stat = os.fstat(stage_descriptor)
            directories = set(ARTIFACT_PREFIXES.values())
            for directory in ARTIFACT_PREFIXES.values():
                directory_descriptor = _open_or_create_output_directory(stage_descriptor, directory)
                os.close(directory_descriptor)
            for entry in entries:
                if entry["type"] != "file":
                    continue
                artifact_path = entry["artifact_path"]
                artifact_pieces = _relative_parts(artifact_path, max_depth=SNAPSHOT_DEPTH + 1)
                for length in range(1, len(artifact_pieces)):
                    directories.add("/".join(artifact_pieces[:length]))
                data = _read_regular_relative(root_descriptor, entry["path"])
                if _digest(data, "snapshot_entry") != entry["content_hash"]:
                    raise SnapshotError(f"source changed during snapshot: {entry['path']}")
                _write_output_file(stage_descriptor, artifact_path, data)
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
                _write_output_file(stage_descriptor, artifact_path, data)
            current_paths: set[str] = set()
            current_budget = [MAX_ENTRIES]
            for scope_path in scope_paths:
                current_paths.update(_entry_paths(root_descriptor, scope_path, current_budget))
            current_entries: list[dict[str, Any]] = []
            for relative in sorted(current_paths):
                current_entry = _describe_entry(root_descriptor, relative)
                current_entry["artifact_path"] = f"{ARTIFACT_PREFIXES['files']}/{relative}" if current_entry["type"] == "file" else None
                current_entries.append(current_entry)
            if current_entries != entries:
                raise SnapshotError("source workspace changed during snapshot")
            if _git_identity(root_descriptor) != base_git_identity:
                raise SnapshotError("Git identity changed during snapshot")
            _assert_directory_path_binding(root, root_descriptor, "repository root")
            _set_output_directory_modes(stage_descriptor, directories)
            _write_output_file(stage_descriptor, "manifest.json", manifest_bytes)
            os.fchmod(stage_descriptor, 0o500)
            _verify_artifact_descriptor(
                stage_descriptor,
                output.parent / stage_name,
                expected_scope=(scope_paths, scope_digest),
                expected_content_identity=manifest["content_identity"],
                expected_manifest_identity=manifest["manifest_identity"],
            )
            _rename_noreplace(output_parent, stage_name, output_name)
            published = True
            final_stat = os.stat(output_name, dir_fd=output_parent, follow_symlinks=False)
            if not _same_inode(final_stat, stage_stat):
                raise SnapshotError("published artifact path is not bound to the staged inode")
            _assert_directory_path_binding(output, stage_descriptor, "published artifact")
            _assert_directory_path_binding(root, root_descriptor, "repository root")
        except Exception:
            if stage_stat is not None:
                cleanup_name = output_name if published else stage_name
                _remove_owned_directory(output_parent, cleanup_name, stage_stat)
            raise
        finally:
            if stage_descriptor is not None:
                os.close(stage_descriptor)
            os.close(output_parent)
        return {
            "artifact": str(output),
            "content_identity": manifest["content_identity"],
            "manifest_identity": manifest["manifest_identity"],
            "scope_digest": scope_digest,
        }
    finally:
        os.close(root_descriptor)


def _read_manifest_from_descriptor(artifact_descriptor: int, artifact: Path) -> dict[str, Any]:
    try:
        manifest_descriptor = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=artifact_descriptor)
    except OSError as exc:
        raise SnapshotError("artifact manifest is missing or is not safely readable") from exc
    try:
        manifest_bytes = _read_open_descriptor(
            manifest_descriptor, "artifact manifest", max_bytes=MANIFEST_BYTES
        )
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


def _verify_artifact_descriptor(
    artifact_descriptor: int,
    artifact: Path,
    *,
    expected_scope: tuple[list[str], str] | None = None,
    expected_content_identity: str | None = None,
    expected_manifest_identity: str | None = None,
) -> dict[str, Any]:
    initial_stat = os.fstat(artifact_descriptor)
    manifest = _read_manifest_from_descriptor(artifact_descriptor, artifact)
    if expected_scope is not None:
        expected_paths, expected_digest = expected_scope
        if manifest["scope_paths"] != expected_paths or manifest["scope_digest"] != expected_digest:
            raise SnapshotError("artifact scope does not match the supplied canonical scope")
    if expected_content_identity is not None and manifest["content_identity"] != expected_content_identity:
        raise SnapshotError("artifact content_identity does not match the expected identity")
    if expected_manifest_identity is not None and manifest["manifest_identity"] != expected_manifest_identity:
        raise SnapshotError("artifact manifest_identity does not match the expected identity")
    _assert_private_stat(initial_stat, directory=True, label="artifact directory")
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

    def collect_tree() -> tuple[set[str], set[str]]:
        files: set[str] = set()
        directories: set[str] = set()
        budget = [MAX_ENTRIES * 2 + len(ARTIFACT_PREFIXES)]
        for root_name in ARTIFACT_PREFIXES.values():
            files_descriptor = os.open(root_name, _directory_flags(), dir_fd=artifact_descriptor)
            try:
                tree_files, tree_directories = _collect_artifact_tree(
                    files_descriptor, root_name, budget
                )
            finally:
                os.close(files_descriptor)
            files.update(tree_files)
            directories.update(tree_directories)
        return files, directories

    actual_files, actual_directories = collect_tree()
    if actual_files != expected_files:
        raise SnapshotError(f"artifact file set mismatch (missing={sorted(expected_files - actual_files)}, extra={sorted(actual_files - expected_files)})")
    if actual_directories != expected_directories:
        raise SnapshotError(f"artifact directory set mismatch (missing={sorted(expected_directories - actual_directories)}, extra={sorted(actual_directories - expected_directories)})")
    total_bytes = 0
    for entry in all_entries:
        if entry["type"] != "file":
            continue
        data = _read_artifact_file(artifact_descriptor, entry["artifact_path"])
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_BYTES:
            raise SnapshotError("artifact content exceeds the declared total byte limit")
        if len(data) != entry["size"] or _digest(data, "snapshot_entry") != entry["content_hash"]:
            raise SnapshotError(f"artifact content mismatch: {entry['path']}")
    final_top_level = set(_directory_listing(artifact_descriptor, "artifact root"))
    final_files, final_directories = collect_tree()
    if final_top_level != top_level or final_files != actual_files or final_directories != actual_directories:
        raise SnapshotError("artifact file or directory set changed during verification")
    for entry in all_entries:
        if entry["type"] != "file":
            continue
        data = _read_artifact_file(artifact_descriptor, entry["artifact_path"])
        if len(data) != entry["size"] or _digest(data, "snapshot_entry") != entry["content_hash"]:
            raise SnapshotError(f"artifact content changed during verification: {entry['path']}")
    final_manifest = _read_manifest_from_descriptor(artifact_descriptor, artifact)
    if final_manifest != manifest or not _same_inode(initial_stat, os.fstat(artifact_descriptor)):
        raise SnapshotError("artifact changed during verification")
    return manifest


def verify_artifact(
    artifact_arg: str,
    scope_arg: str | None = None,
    expected_content_identity: str | None = None,
    expected_manifest_identity: str | None = None,
) -> dict[str, Any]:
    _reject_path_argument_traversal(artifact_arg, label="artifact path")
    artifact = Path(os.path.abspath(artifact_arg))
    if expected_content_identity is not None:
        _ensure_hash(expected_content_identity, "expected_content_identity")
    if expected_manifest_identity is not None:
        _ensure_hash(expected_manifest_identity, "expected_manifest_identity")
    expected_scope = _expected_scope(scope_arg)
    artifact_descriptor = _open_absolute_directory(artifact)
    try:
        manifest = _verify_artifact_descriptor(
            artifact_descriptor,
            artifact,
            expected_scope=expected_scope,
            expected_content_identity=expected_content_identity,
            expected_manifest_identity=expected_manifest_identity,
        )
        _assert_directory_path_binding(artifact, artifact_descriptor, "artifact")
    finally:
        os.close(artifact_descriptor)
    return {
        "artifact": str(artifact),
        "valid": True,
        "content_identity": manifest["content_identity"],
        "manifest_identity": manifest["manifest_identity"],
    }


def compare_workspace(
    root_arg: str,
    artifact_arg: str,
    scope_arg: str | None = None,
    expected_content_identity: str | None = None,
    expected_manifest_identity: str | None = None,
) -> tuple[int, dict[str, Any]]:
    _reject_path_argument_traversal(artifact_arg, label="artifact path")
    artifact = Path(os.path.abspath(artifact_arg))
    if expected_content_identity is not None:
        _ensure_hash(expected_content_identity, "expected_content_identity")
    if expected_manifest_identity is not None:
        _ensure_hash(expected_manifest_identity, "expected_manifest_identity")
    expected_scope = _expected_scope(scope_arg)
    artifact_descriptor = _open_absolute_directory(artifact)
    root_descriptor = -1
    try:
        root = _real_directory(root_arg, label="repository root")
        root_descriptor = _open_root_fd(root)
        manifest = _verify_artifact_descriptor(
            artifact_descriptor,
            artifact,
            expected_scope=expected_scope,
            expected_content_identity=expected_content_identity,
            expected_manifest_identity=expected_manifest_identity,
        )
        actual_git_identity = _git_identity(root_descriptor)
        artifact_result = {
            "artifact": str(artifact),
            "valid": True,
            "content_identity": manifest["content_identity"],
            "manifest_identity": manifest["manifest_identity"],
        }
        code, mismatch, current_entries = _compare_workspace_entries(root_descriptor, manifest)
        if code:
            mismatch["artifact"] = str(artifact)
            return code, mismatch
        current_identity = _content_identity(
            {
                "version": MANIFEST_VERSION,
                "base_git_identity": manifest["base_git_identity"],
                "scope_paths": manifest["scope_paths"],
                "scope_digest": manifest["scope_digest"],
                "entries": current_entries,
                "baseline_entries": manifest["baseline_entries"],
            }
        )
        if current_identity != manifest["content_identity"]:
            return 1, {
                "artifact": str(artifact),
                "match": False,
                "reason": "CONTENT_IDENTITY_MISMATCH",
                "expected": manifest["content_identity"],
                "actual": current_identity,
            }
        second_code, second_mismatch, second_entries = _compare_workspace_entries(root_descriptor, manifest)
        if second_code:
            second_mismatch["artifact"] = str(artifact)
            return second_code, second_mismatch
        if second_entries != current_entries:
            return 1, {
                "artifact": str(artifact),
                "match": False,
                "reason": "WORKSPACE_CHANGED_DURING_COMPARE",
            }
        final_git_identity = _git_identity(root_descriptor)
        if final_git_identity != actual_git_identity:
            return 1, {
                "artifact": str(artifact),
                "match": False,
                "reason": "GIT_IDENTITY_CHANGED_DURING_COMPARE",
                "expected": actual_git_identity,
                "actual": final_git_identity,
            }
        _assert_directory_path_binding(root, root_descriptor, "repository root")
        final_manifest = _verify_artifact_descriptor(
            artifact_descriptor,
            artifact,
            expected_scope=expected_scope,
            expected_content_identity=manifest["content_identity"],
            expected_manifest_identity=manifest["manifest_identity"],
        )
        if final_manifest != manifest:
            raise SnapshotError("artifact changed during workspace comparison")
        _assert_directory_path_binding(artifact, artifact_descriptor, "artifact")
        _assert_directory_path_binding(root, root_descriptor, "repository root")
        result = dict(artifact_result)
        result.update({"match": True, "workspace": str(root)})
        return 0, result
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        os.close(artifact_descriptor)


def _compare_workspace_entries(
    root_descriptor: int, manifest: dict[str, Any]
) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    actual_git_identity = _git_identity(root_descriptor)
    if actual_git_identity != manifest["base_git_identity"]:
        return 1, {
            "match": False,
            "reason": "GIT_IDENTITY_MISMATCH",
            "expected": manifest["base_git_identity"],
            "actual": actual_git_identity,
        }, []
    expected_by_path = {entry["path"]: entry for entry in manifest["entries"]}
    current_paths: set[str] = set()
    budget = [MAX_ENTRIES]
    for scope_path in manifest["scope_paths"]:
        current_paths.update(_entry_paths(root_descriptor, scope_path, budget))
    if current_paths != set(expected_by_path):
        return 1, {
            "match": False,
            "reason": "WORKSPACE_PATH_SET_MISMATCH",
            "missing": sorted(set(expected_by_path) - current_paths),
            "extra": sorted(current_paths - set(expected_by_path)),
        }, []
    mismatches: list[str] = []
    current_entries: list[dict[str, Any]] = []
    for relative in sorted(current_paths):
        current_entry = _describe_entry(root_descriptor, relative)
        current_entry["artifact_path"] = expected_by_path[relative]["artifact_path"]
        current_entries.append(current_entry)
        if current_entry != expected_by_path[relative]:
            mismatches.append(relative)
    if mismatches:
        return 1, {
            "match": False,
            "reason": "WORKSPACE_CONTENT_MISMATCH",
            "paths": mismatches,
        }, []
    return 0, {}, current_entries


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
    verify.add_argument("--expected-content-identity")
    verify.add_argument("--expected-manifest-identity")
    compare = subparsers.add_parser("compare")
    compare.add_argument("root")
    compare.add_argument("artifact")
    compare.add_argument("--scope", help="expected ContractV2 impact-scope JSON")
    compare.add_argument("--expected-content-identity")
    compare.add_argument("--expected-manifest-identity")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_snapshot(args.root, args.scope, args.output)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            return 0
        if args.command == "verify":
            print(
                json.dumps(
                    verify_artifact(
                        args.artifact,
                        args.scope,
                        args.expected_content_identity,
                        args.expected_manifest_identity,
                    ),
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
            return 0
        code, result = compare_workspace(
            args.root,
            args.artifact,
            args.scope,
            args.expected_content_identity,
            args.expected_manifest_identity,
        )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return code
    except (ContractError, SnapshotError, OSError, UnicodeError, TypeError, KeyError, ValueError, RecursionError) as exc:
        return _print_error(exc)


if __name__ == "__main__":
    sys.exit(main())
