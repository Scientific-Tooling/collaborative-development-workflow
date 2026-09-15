#!/usr/bin/env python3
"""Run one Codex Skill behavior case in an isolated Git repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


MAX_RUNNER_OUTPUT = 4 * 1024 * 1024
MAX_CASE_BYTES = 1024 * 1024
MAX_CASE_NESTING = 64
MAX_INVENTORY_ENTRIES = 100_000
MAX_INVENTORY_FILE_BYTES = 256 * 1024 * 1024
MAX_INVENTORY_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_XATTRS_PER_ENTRY = 128
MAX_INVENTORY_XATTRS = 100_000
MAX_XATTR_NAME_BYTES = 4 * 1024
MAX_XATTR_VALUE_BYTES = 64 * 1024
MAX_INVENTORY_XATTR_BYTES = 64 * 1024 * 1024
MAX_PROCESS_ENVIRONMENT_BYTES = 4 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024
CACHE_DIRECTORY_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", "htmlcov"}
)
CACHE_FILE_NAMES = frozenset({".coverage"})
SAFE_VALIDATION_ENVIRONMENT_KEYS = frozenset(
    {"LANG", "LC_ALL", "LC_CTYPE", "PATH", "TZ"}
)
VALIDATION_ISOLATIONS = frozenset({"docker", "unsafe-direct"})
RUNNER_ISOLATIONS = frozenset({"docker", "unsafe-direct"})
DOCKER_VALIDATION_PATH = "/usr/local/bin:/usr/bin:/bin"
DOCKER_CLEANUP_RACE_SECONDS = 30.0
DOCKER_CLEANUP_TIMEOUT_SECONDS = 75.0
DOCKER_CLEANUP_POLL_SECONDS = 0.2
DOCKER_CONTAINER_LABEL_KEY = "com.openai.cdw.behavior-container"
TEMPORARY_CLEANUP_TIMEOUT_SECONDS = 30.0
TEMPORARY_CLEANUP_MEMORY_BYTES = 256 * 1024 * 1024
TEMPORARY_CLEANUP_CPU_SECONDS = 25
TEMPORARY_CLEANUP_OPEN_FILES = 512
TEMPORARY_CLEANUP_MAX_ENTRIES = MAX_INVENTORY_ENTRIES
TEMPORARY_CLEANUP_MAX_DEPTH = MAX_INVENTORY_ENTRIES
RUNNER_CONTAINER_CREDENTIAL_KEYS = ("OPENAI_API_KEY",)
PATH_IDENTIFIER_DOMAIN = b"cdw-behavior-eval-path-v1\0"
XATTR_IDENTITY_DOMAIN = b"cdw-behavior-eval-xattrs-v1\0"
HARDLINK_IDENTITY_DOMAIN = b"cdw-behavior-eval-hardlink-v1\0"
TRUSTED_GIT_EXECUTABLE = shutil.which("git", path=os.defpath)
GIT_SAFETY_ARGUMENTS = (
    "--no-optional-locks",
    "--literal-pathspecs",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.hooksPath=/dev/null",
)


class EvalError(ValueError):
    """A bounded behavior-evaluation failure."""


class _ProcessStopped(EvalError):
    """A process stopped by an evaluator resource limit."""

    def __init__(self, message: str, stdout: bytes, stderr: bytes) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class _ProcessTimedOut(_ProcessStopped):
    """A process exceeded its time limit."""


class _ProcessOutputLimit(_ProcessStopped):
    """A process exceeded its combined output limit."""


class _CaseDeadlineExpired(EvalError):
    """A non-process evaluation phase exceeded the case deadline."""


class _ContainerCleanupUnconfirmed(EvalError):
    """Docker teardown could not be confirmed within its bounded window."""


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EvalError(f"behavior case contains duplicate key: {key!r}")
        value[key] = item
    return value


def _validate_case_text(value: str, field: str) -> None:
    if "\0" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise EvalError(f"{field} contains a NUL or surrogate code point")


def _validate_path_text(path: Path, field: str) -> None:
    value = os.fspath(path)
    if not isinstance(value, str):
        raise EvalError(f"{field} must be a text path")
    _validate_case_text(value, field)


def _validate_case_nesting(value: Any) -> None:
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_CASE_NESTING:
            raise EvalError("behavior case exceeds the nesting limit")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _signal_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        process.kill()


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    _signal_process_group(process)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise EvalError("process could not be reaped after termination") from exc


def _run(
    argv: list[str],
    cwd: Path,
    *,
    timeout: int | float,
    capture_limit: int = MAX_RUNNER_OUTPUT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a process with bounded output and a killable process group."""
    if capture_limit < 0:
        raise EvalError("process output limit must not be negative")
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy() if env is None else env,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    streams = {process.stdout.fileno(): ("stdout", process.stdout)}
    streams[process.stderr.fileno()] = ("stderr", process.stderr)
    selector = selectors.DefaultSelector()
    for descriptor in streams:
        selector.register(descriptor, selectors.EVENT_READ)
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    captured = 0
    deadline = time.monotonic() + timeout
    stopped = False
    try:
        while selector.get_map() or process.poll() is None:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                stopped = True
                raise _ProcessTimedOut(
                    "process timed out", b"".join(chunks["stdout"]), b"".join(chunks["stderr"])
                )
            if not selector.get_map():
                time.sleep(min(0.02, remaining_time))
                continue
            events = selector.select(timeout=min(0.1, remaining_time))
            for key, _ in events:
                descriptor = key.fd
                try:
                    chunk = os.read(descriptor, READ_CHUNK_SIZE)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(descriptor)
                    streams[descriptor][1].close()
                    continue
                room = capture_limit - captured
                if len(chunk) > room:
                    if room:
                        chunks[streams[descriptor][0]].append(chunk[:room])
                    stopped = True
                    raise _ProcessOutputLimit(
                        "process output exceeded the evaluation limit",
                        b"".join(chunks["stdout"]),
                        b"".join(chunks["stderr"]),
                    )
                chunks[streams[descriptor][0]].append(chunk)
                captured += len(chunk)
        returncode = process.wait()
        # A successful command can leave background children in its process group.
        # Stop them before inspecting the workspace.
        _signal_process_group(process)
        return subprocess.CompletedProcess(
            argv,
            returncode,
            b"".join(chunks["stdout"]),
            b"".join(chunks["stderr"]),
        )
    except BaseException:
        if stopped or process.poll() is None:
            _stop_process_group(process)
        else:
            _signal_process_group(process)
        raise
    finally:
        selector.close()
        for _, stream in streams.values():
            if not stream.closed:
                stream.close()


def _load_case(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOCTTY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise EvalError("safe behavior-case reads require O_NOFOLLOW support")
    flags |= no_follow
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvalError(f"cannot safely open behavior case: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise EvalError("behavior case must be a regular file")
        if opened.st_size > MAX_CASE_BYTES:
            raise EvalError("behavior case exceeds the size limit")
        data = bytearray()
        while True:
            remaining = MAX_CASE_BYTES + 1 - len(data)
            chunk = os.read(descriptor, min(READ_CHUNK_SIZE, remaining))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_CASE_BYTES:
                raise EvalError("behavior case exceeds the size limit")
        final = os.fstat(descriptor)
        if (
            opened.st_size != final.st_size
            or opened.st_mtime_ns != final.st_mtime_ns
            or opened.st_ctime_ns != final.st_ctime_ns
        ):
            raise EvalError("behavior case changed while being read")
    except OSError as exc:
        raise EvalError(f"cannot safely read behavior case: {exc}") from exc
    finally:
        os.close(descriptor)
    try:
        value = json.loads(
            bytes(data).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except RecursionError as exc:
        raise EvalError("behavior case exceeds the nesting limit") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvalError(f"cannot load behavior case: {exc}") from exc
    _validate_case_nesting(value)
    required = {
        "version",
        "id",
        "prompt",
        "allowed_changed_paths",
        "required_changed_paths",
        "validation_commands",
        "required_output_lines",
        "timeout_seconds",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise EvalError("behavior case has missing or unknown fields")
    if value["version"] != "skill-behavior-case-v1":
        raise EvalError("unsupported behavior case version")
    if not isinstance(value["id"], str) or not value["id"]:
        raise EvalError("case id must be nonempty text")
    _validate_case_text(value["id"], "case id")
    if not isinstance(value["prompt"], str) or not value["prompt"]:
        raise EvalError("case prompt must be nonempty text")
    _validate_case_text(value["prompt"], "case prompt")
    for field in (
        "allowed_changed_paths",
        "required_changed_paths",
        "required_output_lines",
    ):
        if not isinstance(value[field], list) or not all(
            isinstance(item, str) and item for item in value[field]
        ):
            raise EvalError(f"{field} must be a list of nonempty strings")
        for item in value[field]:
            _validate_case_text(item, field)
    for field in (
        "allowed_changed_paths",
        "required_changed_paths",
        "required_output_lines",
    ):
        if len(value[field]) != len(set(value[field])):
            raise EvalError(f"{field} must not contain duplicates")
    for item in value["required_output_lines"]:
        if item != item.strip() or "\n" in item or "\r" in item:
            raise EvalError("required_output_lines must contain stripped single lines")
    for field in ("allowed_changed_paths", "required_changed_paths"):
        for item in value[field]:
            candidate = Path(item)
            if candidate.is_absolute() or item != candidate.as_posix() or ".." in candidate.parts:
                raise EvalError(f"{field} must contain normalized relative paths")
            if not candidate.parts or candidate.parts[0] == ".git":
                raise EvalError(f"{field} must not name Git control files")
    if not set(value["required_changed_paths"]).issubset(
        value["allowed_changed_paths"]
    ):
        raise EvalError("required_changed_paths must be allowed")
    commands = value["validation_commands"]
    if not isinstance(commands, list) or not all(
        isinstance(command, list)
        and command
        and all(isinstance(item, str) and item for item in command)
        for command in commands
    ):
        raise EvalError("validation_commands must contain nonempty argument lists")
    for command in commands:
        for item in command:
            _validate_case_text(item, "validation command")
    timeout = value["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        raise EvalError("timeout_seconds must be an integer from 1 through 3600")
    return value


def _safe_inherited_environment() -> dict[str, str]:
    """Return the small set of host values safe to pass to trusted helpers."""
    return {
        key: value
        for key, value in os.environ.items()
        if key in SAFE_VALIDATION_ENVIRONMENT_KEYS
    }


def _minimal_validation_environment(home: Path) -> dict[str, str]:
    """Build a validation environment without inheriting credential or startup hooks."""
    environment = _safe_inherited_environment()
    environment["PATH"] = os.defpath
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_RUNTIME_DIR": str(home / ".runtime"),
            "TMPDIR": str(home / ".tmp"),
            "GIT_OPTIONAL_LOCKS": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


def _process_environment_names() -> set[str]:
    """Read current and initially inherited names without retaining their values."""
    names = set(os.environ)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open("/proc/self/environ", flags)
    except OSError as exc:
        raise EvalError(
            "direct validation cannot verify the evaluator process environment"
        ) from exc
    data = bytearray()
    try:
        while True:
            remaining = MAX_PROCESS_ENVIRONMENT_BYTES + 1 - len(data)
            chunk = os.read(descriptor, min(READ_CHUNK_SIZE, remaining))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_PROCESS_ENVIRONMENT_BYTES:
                raise EvalError("evaluator process environment exceeds the inspection limit")
    finally:
        os.close(descriptor)
    for item in bytes(data).split(b"\0"):
        raw_name, separator, _ = item.partition(b"=")
        if separator and raw_name:
            names.add(os.fsdecode(raw_name))
    return names


def _unsafe_inherited_environment_names(names: set[str] | None = None) -> list[str]:
    inspected = _process_environment_names() if names is None else names
    return sorted(inspected - SAFE_VALIDATION_ENVIRONMENT_KEYS)


def _git_environment() -> dict[str, str]:
    environment = _safe_inherited_environment()
    environment["PATH"] = os.defpath
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["LC_ALL"] = "C"
    return environment


def _remaining_case_time(deadline: float, phase: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _CaseDeadlineExpired(f"case deadline expired during {phase}")
    return remaining


def _git(workspace: Path, *args: str, timeout: int | float = 60) -> bytes:
    if TRUSTED_GIT_EXECUTABLE is None:
        raise EvalError("Git is not available on the evaluator's trusted PATH")
    result = _run(
        [TRUSTED_GIT_EXECUTABLE, *GIT_SAFETY_ARGUMENTS, *args],
        workspace,
        timeout=timeout,
        env=_git_environment(),
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace")[:1024]
        raise EvalError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _tracked_regular_files(
    source: Path, deadline: float | None = None
) -> list[tuple[Path, int]]:
    timeout = (
        60
        if deadline is None
        else min(60.0, _remaining_case_time(deadline, "tracked-file discovery"))
    )
    top_level = _git(source, "rev-parse", "--show-toplevel", timeout=timeout)
    try:
        discovered_root = Path(os.fsdecode(top_level.rstrip(b"\n"))).resolve()
    except (OSError, UnicodeError) as exc:
        raise EvalError(f"cannot resolve Git source root: {exc}") from exc
    if discovered_root != source:
        raise EvalError("source root must be the root of its Git worktree")

    timeout = (
        60
        if deadline is None
        else min(60.0, _remaining_case_time(deadline, "tracked-file discovery"))
    )
    output = _git(source, "ls-files", "--stage", "-z", timeout=timeout)
    files: list[tuple[Path, int]] = []
    seen: set[Path] = set()
    for raw_record in output.split(b"\0"):
        if not raw_record:
            continue
        if deadline is not None:
            _remaining_case_time(deadline, "tracked-file discovery")
        if len(files) >= MAX_INVENTORY_ENTRIES:
            raise EvalError("source has too many tracked files")
        try:
            raw_metadata, raw_path = raw_record.split(b"\t", 1)
            raw_mode, _object_id, raw_stage = raw_metadata.split(b" ", 2)
            relative = Path(os.fsdecode(raw_path))
            mode = int(raw_mode, 8)
            stage = int(raw_stage)
        except (ValueError, UnicodeError) as exc:
            raise EvalError("Git returned an invalid tracked-file entry") from exc
        if stage != 0:
            raise EvalError("source index has unmerged entries")
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or relative.parts[0] == ".git"
        ):
            raise EvalError("Git returned an unsafe tracked path")
        if relative in seen:
            raise EvalError("Git returned a duplicate tracked path")
        seen.add(relative)
        if mode not in (0o100644, 0o100755):
            raise EvalError(f"tracked path is not a regular file: {relative.as_posix()}")
        files.append((relative, mode))
    return sorted(files, key=lambda item: os.fsencode(item[0].as_posix()))


def _open_tracked_regular_file(
    source_descriptor: int, source_root: Path, relative: Path
) -> tuple[int, os.stat_result]:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    current_descriptor = os.dup(source_descriptor)
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=current_descriptor,
            )
            os.close(current_descriptor)
            current_descriptor = next_descriptor
            if not stat.S_ISDIR(os.fstat(current_descriptor).st_mode):
                raise EvalError(
                    f"tracked path parent is not a directory: {source_root / relative}"
                )
        before_open = os.stat(
            relative.parts[-1], dir_fd=current_descriptor, follow_symlinks=False
        )
        if not stat.S_ISREG(before_open.st_mode):
            raise EvalError(
                f"tracked path is not a regular file: {source_root / relative}"
            )
        file_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        file_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            relative.parts[-1], file_flags, dir_fd=current_descriptor
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or before_open.st_dev != opened.st_dev
            or before_open.st_ino != opened.st_ino
        ):
            os.close(descriptor)
            raise EvalError(
                f"tracked path changed while being opened: {source_root / relative}"
            )
        return descriptor, opened
    except OSError as exc:
        raise EvalError(
            f"cannot safely open tracked file {source_root / relative}: {exc}"
        ) from exc
    finally:
        os.close(current_descriptor)


def _copy_regular_file(
    source_descriptor: int,
    source_root: Path,
    relative: Path,
    destination: Path,
    executable: bool,
    budget: list[int],
    deadline: float,
) -> None:
    _remaining_case_time(deadline, "workspace preparation")
    descriptor, opened_status = _open_tracked_regular_file(
        source_descriptor, source_root, relative
    )
    if opened_status.st_size > MAX_INVENTORY_FILE_BYTES:
        os.close(descriptor)
        raise EvalError(f"tracked file exceeds the copy size limit: {source_root / relative}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(descriptor, "rb", closefd=True) as source_stream:
            with destination.open("xb") as destination_stream:
                while True:
                    _remaining_case_time(deadline, "workspace preparation")
                    chunk = source_stream.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    budget[0] += len(chunk)
                    if budget[0] > MAX_INVENTORY_TOTAL_BYTES:
                        raise EvalError("tracked files exceed the total copy size limit")
                    destination_stream.write(chunk)
            final_status = os.fstat(source_stream.fileno())
            if (
                opened_status.st_size != final_status.st_size
                or opened_status.st_mtime_ns != final_status.st_mtime_ns
                or opened_status.st_ctime_ns != final_status.st_ctime_ns
            ):
                raise EvalError(
                    f"tracked path changed while being copied: {source_root / relative}"
                )
        destination.chmod(0o755 if executable else 0o644)
    except BaseException:
        if not destination.exists():
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def _prepare_workspace(source: Path, workspace: Path, deadline: float) -> None:
    _remaining_case_time(deadline, "workspace preparation")
    source = source.resolve()
    workspace = workspace.resolve(strict=False)
    if workspace == source or source in workspace.parents:
        raise EvalError("workspace must be outside the source tree")
    if os.path.lexists(workspace):
        raise EvalError(f"workspace already exists: {workspace}")

    root_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    root_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        source_status = source.lstat()
        source_descriptor = os.open(source, root_flags)
    except OSError as exc:
        raise EvalError(f"cannot safely open source root {source}: {exc}") from exc
    try:
        opened_source_status = os.fstat(source_descriptor)
        if (
            not stat.S_ISDIR(opened_source_status.st_mode)
            or source_status.st_dev != opened_source_status.st_dev
            or source_status.st_ino != opened_source_status.st_ino
        ):
            raise EvalError("source root changed while being opened")
        tracked_files = _tracked_regular_files(source, deadline)
        current_source_status = source.lstat()
        if (
            current_source_status.st_dev != opened_source_status.st_dev
            or current_source_status.st_ino != opened_source_status.st_ino
        ):
            raise EvalError("source root changed while tracked files were listed")
        workspace.mkdir(parents=True)
        copy_budget = [0]
        for relative, mode in tracked_files:
            _remaining_case_time(deadline, "workspace preparation")
            _copy_regular_file(
                source_descriptor,
                source,
                relative,
                workspace / relative,
                mode == 0o100755,
                copy_budget,
                deadline,
            )
    finally:
        os.close(source_descriptor)

    for command in (
        ("init", "-q", "--initial-branch=main"),
        ("config", "user.name", "Codex Skill Eval"),
        ("config", "user.email", "skill-eval@example.invalid"),
        ("add", "--all"),
        ("commit", "-qm", "behavior evaluation baseline"),
    ):
        _git(
            workspace,
            *command,
            timeout=min(
                60.0, _remaining_case_time(deadline, "workspace preparation")
            ),
        )


def _directory_identity(path: Path, label: str) -> tuple[int, int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise EvalError(f"safe {label} pinning requires O_NOFOLLOW support")
    flags |= no_follow
    try:
        before = path.lstat()
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvalError(f"cannot safely pin {label}") from exc
    try:
        opened = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISDIR(before.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or before.st_dev != opened.st_dev
        or before.st_ino != opened.st_ino
    ):
        raise EvalError(f"{label} changed while its identity was pinned")
    return opened.st_dev, opened.st_ino


def _workspace_root_identity(workspace: Path) -> tuple[int, int]:
    return _directory_identity(workspace, "workspace root")


def _verify_workspace_root(
    workspace: Path, expected: tuple[int, int], phase: str
) -> None:
    if _workspace_root_identity(workspace) != expected:
        raise EvalError(f"workspace root changed {phase}")


def _git_directory_identity(workspace: Path) -> tuple[int, int]:
    return _directory_identity(workspace / ".git", "Git directory")


def _verify_git_directory(
    workspace: Path, expected: tuple[int, int], phase: str
) -> None:
    if _git_directory_identity(workspace) != expected:
        raise EvalError(f"Git directory changed {phase}")


def _is_cache_artifact(relative: Path) -> bool:
    return bool(
        any(part in CACHE_DIRECTORY_NAMES for part in relative.parts)
        or relative.name in CACHE_FILE_NAMES
        or relative.name.endswith(".pyc")
    )


def _xattr_identity(
    target: int | Path,
    budget: list[int],
    deadline: float | None,
    deadline_phase: str,
    *,
    follow_symlinks: bool | None = None,
) -> str:
    if not hasattr(os, "listxattr") or not hasattr(os, "getxattr"):
        raise EvalError("extended-attribute inspection is not supported")
    keyword_arguments = (
        {} if follow_symlinks is None else {"follow_symlinks": follow_symlinks}
    )
    try:
        names = os.listxattr(target, **keyword_arguments)
    except (OSError, TypeError, ValueError) as exc:
        raise EvalError("cannot inspect workspace extended attributes") from exc
    if len(names) > MAX_XATTRS_PER_ENTRY:
        raise EvalError("workspace entry has too many extended attributes")

    encoded_names: list[tuple[bytes, str]] = []
    for name in names:
        if deadline is not None:
            _remaining_case_time(deadline, deadline_phase)
        encoded = os.fsencode(name)
        if len(encoded) > MAX_XATTR_NAME_BYTES:
            raise EvalError("workspace extended-attribute name exceeds the size limit")
        encoded_names.append((encoded, name))
    encoded_names.sort(key=lambda item: item[0])
    if len({name for name, _text in encoded_names}) != len(encoded_names):
        raise EvalError("workspace entry has duplicate extended-attribute names")

    identity = hashlib.sha256(XATTR_IDENTITY_DOMAIN)
    for encoded_name, name in encoded_names:
        if deadline is not None:
            _remaining_case_time(deadline, deadline_phase)
        try:
            value = os.getxattr(target, name, **keyword_arguments)
        except (OSError, TypeError, ValueError) as exc:
            raise EvalError("cannot inspect workspace extended attributes") from exc
        if len(value) > MAX_XATTR_VALUE_BYTES:
            raise EvalError("workspace extended-attribute value exceeds the size limit")
        budget[0] += 1
        budget[1] += len(encoded_name) + len(value)
        if budget[0] > MAX_INVENTORY_XATTRS:
            raise EvalError("workspace has too many extended attributes")
        if budget[1] > MAX_INVENTORY_XATTR_BYTES:
            raise EvalError("workspace extended attributes exceed the total size limit")
        identity.update(len(encoded_name).to_bytes(4, "big"))
        identity.update(encoded_name)
        identity.update(len(value).to_bytes(8, "big"))
        identity.update(value)

    try:
        final_names = sorted(
            (os.fsencode(name) for name in os.listxattr(target, **keyword_arguments))
        )
    except (OSError, TypeError, ValueError) as exc:
        raise EvalError("cannot recheck workspace extended attributes") from exc
    if final_names != [name for name, _text in encoded_names]:
        raise EvalError("workspace extended attributes changed while being inspected")
    return identity.hexdigest()


def _entry_xattr_identity(
    path: Path,
    status_before: os.stat_result,
    budget: list[int],
    deadline: float | None,
    deadline_phase: str,
) -> str:
    if stat.S_ISLNK(status_before.st_mode):
        identity = _xattr_identity(
            path,
            budget,
            deadline,
            deadline_phase,
            follow_symlinks=False,
        )
        try:
            status_after = path.lstat()
        except OSError as exc:
            raise EvalError("cannot recheck workspace symlink") from exc
    else:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise EvalError("extended-attribute inspection requires O_NOFOLLOW")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow
        if stat.S_ISDIR(status_before.st_mode):
            flags |= os.O_DIRECTORY
        elif stat.S_ISREG(status_before.st_mode):
            flags |= getattr(os, "O_NONBLOCK", 0)
        else:
            raise EvalError("workspace contains an unsupported special entry")
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise EvalError("cannot open workspace entry for attribute inspection") from exc
        try:
            opened = os.fstat(descriptor)
            if (
                status_before.st_dev != opened.st_dev
                or status_before.st_ino != opened.st_ino
                or stat.S_IFMT(status_before.st_mode) != stat.S_IFMT(opened.st_mode)
            ):
                raise EvalError(
                    "workspace entry changed while being opened for attribute inspection"
                )
            identity = _xattr_identity(
                descriptor,
                budget,
                deadline,
                deadline_phase,
            )
            status_after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    if (
        status_before.st_dev != status_after.st_dev
        or status_before.st_ino != status_after.st_ino
        or status_before.st_mode != status_after.st_mode
        or status_before.st_size != status_after.st_size
        or status_before.st_nlink != status_after.st_nlink
        or status_before.st_mtime_ns != status_after.st_mtime_ns
        or status_before.st_ctime_ns != status_after.st_ctime_ns
    ):
        raise EvalError("workspace entry changed during attribute inspection")
    return identity


def _hardlink_group_identifier(status: os.stat_result) -> str:
    identity = f"{status.st_dev}:{status.st_ino}".encode("ascii")
    return hashlib.sha256(HARDLINK_IDENTITY_DOMAIN + identity).hexdigest()


def _hash_regular_file(
    path: Path,
    status: os.stat_result,
    budget: list[int],
    xattr_budget: list[int],
    deadline: float | None = None,
    deadline_phase: str = "filesystem inventory",
) -> tuple[str, str]:
    if status.st_size > MAX_INVENTORY_FILE_BYTES:
        raise EvalError(f"workspace file exceeds the inventory size limit: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvalError(f"cannot read workspace file {path}: {exc}") from exc
    digest = hashlib.sha256()
    consumed = 0
    try:
        opened_status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_status.st_mode)
            or status.st_dev != opened_status.st_dev
            or status.st_ino != opened_status.st_ino
            or status.st_mode != opened_status.st_mode
            or status.st_size != opened_status.st_size
            or status.st_nlink != opened_status.st_nlink
            or status.st_mtime_ns != opened_status.st_mtime_ns
            or status.st_ctime_ns != opened_status.st_ctime_ns
        ):
            raise EvalError(f"workspace entry changed type while being inspected: {path}")
        while True:
            if deadline is not None:
                _remaining_case_time(deadline, deadline_phase)
            chunk = os.read(descriptor, READ_CHUNK_SIZE)
            if not chunk:
                break
            consumed += len(chunk)
            budget[0] += len(chunk)
            if consumed > MAX_INVENTORY_FILE_BYTES:
                raise EvalError(f"workspace file exceeds the inventory size limit: {path}")
            if budget[0] > MAX_INVENTORY_TOTAL_BYTES:
                raise EvalError("workspace exceeds the total inventory size limit")
            digest.update(chunk)
        xattr_digest = _xattr_identity(
            descriptor,
            xattr_budget,
            deadline,
            deadline_phase,
        )
        final_status = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        opened_status.st_size != final_status.st_size
        or opened_status.st_mode != final_status.st_mode
        or opened_status.st_nlink != final_status.st_nlink
        or opened_status.st_mtime_ns != final_status.st_mtime_ns
        or opened_status.st_ctime_ns != final_status.st_ctime_ns
    ):
        raise EvalError(f"workspace file changed while being inspected: {path}")
    return digest.hexdigest(), xattr_digest


def _filesystem_inventory(
    root: Path,
    *,
    exclude_git: bool,
    exclude_caches: bool,
    included_cache_paths: frozenset[str] = frozenset(),
    deadline: float | None = None,
    deadline_phase: str = "filesystem inventory",
) -> dict[str, str]:
    try:
        root_status = root.lstat()
    except OSError as exc:
        raise EvalError("cannot inspect inventory root") from exc
    if not stat.S_ISDIR(root_status.st_mode):
        raise EvalError(f"inventory root is not a directory: {root}")
    included_cache_tree_paths = set(included_cache_paths)
    for tracked in included_cache_paths:
        parts = Path(tracked).parts
        included_cache_tree_paths.update(
            Path(*parts[:index]).as_posix() for index in range(1, len(parts))
        )
    xattr_budget = [0, 0]
    root_xattr_digest = _entry_xattr_identity(
        root,
        root_status,
        xattr_budget,
        deadline,
        deadline_phase,
    )
    inventory: dict[str, str] = {
        ".": (
            f"directory:{stat.S_IMODE(root_status.st_mode):o}:{root_xattr_digest}:"
            f"{_hardlink_group_identifier(root_status)}"
        )
    }
    pending: list[tuple[Path, Path]] = [(root, Path())]
    budget = [0]
    while pending:
        if deadline is not None:
            _remaining_case_time(deadline, deadline_phase)
        directory, relative_directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: os.fsencode(item.name))
        except OSError as exc:
            raise EvalError(f"cannot inspect workspace directory {directory}: {exc}") from exc
        for entry in entries:
            if deadline is not None:
                _remaining_case_time(deadline, deadline_phase)
            relative = relative_directory / entry.name
            path_text = relative.as_posix()
            if exclude_git and relative.parts[0] == ".git":
                continue
            cache_path_is_tracked = path_text in included_cache_tree_paths
            if (
                exclude_caches
                and _is_cache_artifact(relative)
                and not cache_path_is_tracked
            ):
                continue
            if len(inventory) >= MAX_INVENTORY_ENTRIES:
                raise EvalError("workspace has too many entries to inventory")
            try:
                entry_status = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise EvalError(f"cannot inspect workspace entry {entry.path}: {exc}") from exc
            permissions = stat.S_IMODE(entry_status.st_mode)
            if stat.S_ISDIR(entry_status.st_mode):
                xattr_digest = _entry_xattr_identity(
                    Path(entry.path),
                    entry_status,
                    xattr_budget,
                    deadline,
                    deadline_phase,
                )
                inventory[path_text] = (
                    f"directory:{permissions:o}:{xattr_digest}:"
                    f"{_hardlink_group_identifier(entry_status)}"
                )
                pending.append((Path(entry.path), relative))
            elif stat.S_ISREG(entry_status.st_mode):
                content_digest, xattr_digest = _hash_regular_file(
                    Path(entry.path),
                    entry_status,
                    budget,
                    xattr_budget,
                    deadline,
                    deadline_phase,
                )
                inventory[path_text] = (
                    f"file:{permissions:o}:{entry_status.st_size}:"
                    f"{entry_status.st_mtime_ns}:{content_digest}:{xattr_digest}:"
                    f"{entry_status.st_nlink}:"
                    f"{_hardlink_group_identifier(entry_status)}"
                )
            elif stat.S_ISLNK(entry_status.st_mode):
                try:
                    target = os.readlink(entry.path)
                except OSError as exc:
                    raise EvalError(
                        f"cannot inspect workspace symlink {entry.path}: {exc}"
                    ) from exc
                target_digest = hashlib.sha256(os.fsencode(target)).hexdigest()
                xattr_digest = _entry_xattr_identity(
                    Path(entry.path),
                    entry_status,
                    xattr_budget,
                    deadline,
                    deadline_phase,
                )
                inventory[path_text] = (
                    f"symlink:{permissions:o}:{entry_status.st_mtime_ns}:"
                    f"{target_digest}:{xattr_digest}:{entry_status.st_nlink}:"
                    f"{_hardlink_group_identifier(entry_status)}"
                )
            else:
                raise EvalError(
                    f"workspace contains an unsupported special entry: {entry.path}"
                )
    return inventory


def _inventory_digest(inventory: dict[str, str]) -> str:
    rendered = json.dumps(
        inventory, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(rendered).hexdigest()


def _enforce_workspace_budget(
    root: Path, deadline: float, phase: str
) -> dict[str, int]:
    try:
        root_status = root.lstat()
    except OSError as exc:
        raise EvalError("cannot enforce workspace budget at its root") from exc
    if not stat.S_ISDIR(root_status.st_mode):
        raise EvalError("workspace budget root is not a directory")
    xattr_budget = [0, 0]
    _entry_xattr_identity(
        root, root_status, xattr_budget, deadline, phase
    )
    pending = [root]
    entries = 1
    regular_file_bytes = 0
    while pending:
        _remaining_case_time(deadline, phase)
        directory = pending.pop()
        try:
            iterator = os.scandir(directory)
        except OSError as exc:
            raise EvalError(f"cannot enforce workspace budget at {directory}: {exc}") from exc
        with iterator:
            for entry in iterator:
                _remaining_case_time(deadline, phase)
                entries += 1
                if entries > MAX_INVENTORY_ENTRIES:
                    raise EvalError("workspace exceeds the entry limit")
                try:
                    entry_status = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise EvalError(
                        f"cannot enforce workspace budget at {entry.path}: {exc}"
                    ) from exc
                if not (
                    stat.S_ISDIR(entry_status.st_mode)
                    or stat.S_ISREG(entry_status.st_mode)
                    or stat.S_ISLNK(entry_status.st_mode)
                ):
                    raise EvalError(
                        f"workspace contains an unsupported special entry: {entry.path}"
                    )
                _entry_xattr_identity(
                    Path(entry.path),
                    entry_status,
                    xattr_budget,
                    deadline,
                    phase,
                )
                if stat.S_ISDIR(entry_status.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(entry_status.st_mode):
                    if entry_status.st_size > MAX_INVENTORY_FILE_BYTES:
                        raise EvalError(
                            f"workspace file exceeds the size limit: {entry.path}"
                        )
                    regular_file_bytes += entry_status.st_size
                    if regular_file_bytes > MAX_INVENTORY_TOTAL_BYTES:
                        raise EvalError("workspace exceeds the total size limit")
    return {
        "entries": entries,
        "regular_file_bytes": regular_file_bytes,
        "extended_attributes": xattr_budget[0],
        "extended_attribute_bytes": xattr_budget[1],
    }


def _inventory_changes(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    }


def _meaningful_inventory_value(value: str | None) -> str | None:
    if value is None:
        return None
    fields = value.split(":")
    if fields[0] == "file" and len(fields) == 8:
        (
            kind,
            _permissions,
            size,
            _mtime,
            digest,
            _xattr_digest,
            _link_count,
            _hardlink_group,
        ) = fields
        return ":".join((kind, size, digest))
    if fields[0] == "symlink" and len(fields) == 7:
        (
            kind,
            _permissions,
            _mtime,
            target_digest,
            _xattr_digest,
            _link_count,
            _hardlink_group,
        ) = fields
        return ":".join((kind, target_digest))
    if fields[0] == "directory" and len(fields) == 4:
        kind, _permissions, _xattr_digest, _inode_token = fields
        return kind
    return value


def _meaningful_inventory_changes(
    before: dict[str, str], after: dict[str, str]
) -> set[str]:
    return {
        path
        for path in before.keys() | after.keys()
        if _meaningful_inventory_value(before.get(path))
        != _meaningful_inventory_value(after.get(path))
    }


def _opaque_path_identifier(path: str) -> str:
    digest = hashlib.sha256(PATH_IDENTIFIER_DOMAIN + os.fsencode(path)).hexdigest()
    return f"path-sha256:{digest}"


def _reported_paths(
    paths: set[str],
    allowed_paths: set[str],
    force_opaque: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    return sorted(
        path
        if path in allowed_paths and path not in force_opaque
        else _opaque_path_identifier(path)
        for path in paths
    )


def _inspect_runner_controlled_workspace(
    phase: str, operation: Any, *args: Any, **kwargs: Any
) -> Any:
    try:
        return operation(*args, **kwargs)
    except _CaseDeadlineExpired:
        raise
    except (EvalError, OSError) as exc:
        raise EvalError(
            f"{phase} failed while inspecting runner-controlled workspace"
        ) from exc


def _observe_git_command(
    workspace: Path,
    arguments: list[str],
    deadline: float | None = None,
    deadline_phase: str = "repository inspection",
) -> dict[str, Any]:
    if TRUSTED_GIT_EXECUTABLE is None:
        raise EvalError("Git is not available on the evaluator's trusted PATH")
    try:
        timeout = (
            60
            if deadline is None
            else min(
                60.0,
                _remaining_case_time(deadline, deadline_phase),
            )
        )
        result = _run(
            [
                TRUSTED_GIT_EXECUTABLE,
                *GIT_SAFETY_ARGUMENTS,
                *arguments,
            ],
            workspace,
            timeout=timeout,
            env=_git_environment(),
        )
    except (OSError, _ProcessStopped) as exc:
        return {"exit_code": None, "error": type(exc).__name__}
    return {
        "exit_code": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
        "text": result.stdout.decode("utf-8", "replace").strip()
        if result.returncode == 0 and len(result.stdout) <= 1024
        else None,
    }


def _repository_state(
    workspace: Path,
    deadline: float | None = None,
    deadline_phase: str = "repository inspection",
) -> dict[str, Any]:
    git_directory = workspace / ".git"
    metadata = _filesystem_inventory(
        git_directory,
        exclude_git=False,
        exclude_caches=False,
        deadline=deadline,
        deadline_phase=deadline_phase,
    )
    state = {
        "head": _observe_git_command(
            workspace,
            ["rev-parse", "--verify", "HEAD"],
            deadline,
            deadline_phase,
        ),
        "head_ref": _observe_git_command(
            workspace,
            ["symbolic-ref", "--quiet", "HEAD"],
            deadline,
            deadline_phase,
        ),
        "refs": _observe_git_command(
            workspace,
            ["for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)"],
            deadline,
            deadline_phase,
        ),
        "index": metadata.get("index", "missing"),
        "metadata_sha256": _inventory_digest(metadata),
    }
    for field in ("head", "head_ref", "refs"):
        observation = state[field]
        if observation.get("exit_code") is None or "error" in observation:
            raise EvalError(f"cannot complete repository inspection for {field}")
    return state


def _repository_checks(
    baseline: dict[str, Any],
    after_runner: dict[str, Any],
    final: dict[str, Any],
) -> dict[str, bool]:
    fields = {
        "head_unchanged": "head",
        "head_ref_unchanged": "head_ref",
        "refs_unchanged": "refs",
        "index_unchanged": "index",
        "git_metadata_unchanged": "metadata_sha256",
    }
    required_fields = set(fields.values())
    states = (baseline, after_runner, final)
    complete = all(
        set(state) == required_fields
        and all(
            isinstance(state[field], dict)
            and state[field].get("exit_code") is not None
            and "error" not in state[field]
            for field in ("head", "head_ref", "refs")
        )
        for state in states
    )
    return {
        check: complete
        and baseline[field] == after_runner[field] == final[field]
        for check, field in fields.items()
    }


def _combined_output(stdout: bytes, stderr: bytes) -> bytes:
    return stdout + b"\n" + stderr


def _validate_docker_image(image: str | None) -> str:
    if not isinstance(image, str) or not image:
        raise EvalError("Docker isolation requires a safe nonempty image name")
    _validate_case_text(image, "Docker image name")
    if (
        len(image) > 512
        or image.startswith("-")
        or any(character.isspace() or ord(character) < 32 for character in image)
    ):
        raise EvalError("Docker isolation requires a safe nonempty image name")
    return image


def _validate_docker_image_id(image: str | None) -> str:
    validated = _validate_docker_image(image)
    prefix = "sha256:"
    digest = validated[len(prefix) :] if validated.startswith(prefix) else ""
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise EvalError("Docker isolation requires an immutable local sha256 image ID")
    return validated


def _validate_runner_tool_version(version: str | None) -> str:
    if not isinstance(version, str) or not version or len(version) > 256:
        raise EvalError("Docker runner requires a pre-secret observed tool version")
    _validate_case_text(version, "runner tool version")
    if version != version.strip() or "\n" in version or "\r" in version:
        raise EvalError("runner tool version must be stripped single-line text")
    return version


def _validate_docker_container_name(container_name: str) -> None:
    if not isinstance(container_name, str) or not container_name:
        raise EvalError("Docker container name must be nonempty text")
    _validate_case_text(container_name, "Docker container name")
    allowed_punctuation = "_.-"
    if (
        len(container_name) > 128
        or not container_name[0].isascii()
        or not container_name[0].isalnum()
        or any(
            not character.isascii()
            or not (character.isalnum() or character in allowed_punctuation)
            for character in container_name[1:]
        )
    ):
        raise EvalError("Docker container name contains unsupported characters")


def _docker_container_label(container_name: str) -> str:
    _validate_docker_container_name(container_name)
    return f"{DOCKER_CONTAINER_LABEL_KEY}={container_name}"


def _docker_container_id(output: bytes) -> str:
    candidate = output.strip()
    if len(candidate) != 64 or any(
        character not in b"0123456789abcdef" for character in candidate
    ):
        raise EvalError("Docker create returned an invalid container ID")
    return candidate.decode("ascii")


def _docker_validation_argv(
    image: str,
    workspace: Path,
    command: list[str],
    container_name: str,
) -> list[str]:
    workspace_text = str(workspace)
    _validate_case_text(workspace_text, "Docker validation workspace path")
    _validate_docker_image_id(image)
    _validate_docker_container_name(container_name)
    for item in command:
        _validate_case_text(item, "validation command")
    if "," in workspace_text or "\n" in workspace_text or "\r" in workspace_text:
        raise EvalError("docker validation workspace path contains an unsupported character")
    return [
        "docker",
        "container",
        "create",
        "--pull=never",
        f"--name={container_name}",
        f"--label={_docker_container_label(container_name)}",
        "--network=none",
        # Docker's supported default is a private PID namespace. Do not pass
        # --pid=host (or join any other container's namespace).
        "--ipc=private",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--pids-limit=256",
        "--memory=1073741824",
        "--memory-swap=1073741824",
        "--cpus=2",
        "--ulimit=nofile=1024:1024",
        "--ulimit=core=0:0",
        f"--ulimit=fsize={MAX_INVENTORY_FILE_BYTES}:{MAX_INVENTORY_FILE_BYTES}",
        f"--user={os.getuid()}:{os.getgid()}",
        "--workdir=/workspace",
        f"--mount=type=bind,source={workspace_text},target=/workspace",
        "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=268435456,mode=1777",
        "--entrypoint=/usr/bin/env",
        image,
        "-i",
        "HOME=/tmp",
        "USERPROFILE=/tmp",
        "CODEX_HOME=/tmp/.codex",
        "XDG_CONFIG_HOME=/tmp/.config",
        "XDG_DATA_HOME=/tmp/.local/share",
        "XDG_STATE_HOME=/tmp/.local/state",
        "XDG_CACHE_HOME=/tmp/.cache",
        "XDG_RUNTIME_DIR=/tmp/.runtime",
        "TMPDIR=/tmp",
        f"PATH={DOCKER_VALIDATION_PATH}",
        "LC_ALL=C",
        "GIT_CONFIG_NOSYSTEM=1",
        "GIT_CONFIG_GLOBAL=/dev/null",
        "GIT_OPTIONAL_LOCKS=0",
        "PYTHONDONTWRITEBYTECODE=1",
        *command,
    ]


def _docker_runner_argv(
    image: str,
    workspace: Path,
    runner: list[str],
    prompt: str,
    container_name: str,
    container_skill_path: str,
) -> list[str]:
    workspace_text = str(workspace)
    _validate_case_text(workspace_text, "Docker runner workspace path")
    _validate_docker_image_id(image)
    _validate_docker_container_name(container_name)
    for item in runner:
        _validate_case_text(item, "runner argument")
    _validate_case_text(prompt, "runner prompt")
    _validate_case_text(container_skill_path, "Docker runner Skill path")
    if "," in workspace_text or "\n" in workspace_text or "\r" in workspace_text:
        raise EvalError("Docker runner workspace path contains an unsupported character")
    parsed_skill_path = Path(container_skill_path)
    if not (
        parsed_skill_path == Path("/workspace")
        or Path("/workspace") in parsed_skill_path.parents
    ):
        raise EvalError("Docker runner Skill path must be inside /workspace")
    return [
        "docker",
        "container",
        "create",
        "--pull=never",
        f"--name={container_name}",
        f"--label={_docker_container_label(container_name)}",
        "--network=bridge",
        "--ipc=private",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--pids-limit=512",
        "--memory=2147483648",
        "--memory-swap=2147483648",
        "--cpus=2",
        "--ulimit=nofile=2048:2048",
        "--ulimit=core=0:0",
        f"--ulimit=fsize={MAX_INVENTORY_FILE_BYTES}:{MAX_INVENTORY_FILE_BYTES}",
        f"--user={os.getuid()}:{os.getgid()}",
        "--workdir=/workspace",
        f"--mount=type=bind,source={workspace_text},target=/workspace",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=536870912,mode=1777",
        "--env=OPENAI_API_KEY",
        "--env=HOME=/tmp/home",
        "--env=CODEX_HOME=/tmp/home/.codex",
        "--env=TMPDIR=/tmp",
        "--env=GIT_OPTIONAL_LOCKS=0",
        f"--env=CDW_EVAL_SKILL_PATH={container_skill_path}",
        "--env=CDW_EVAL_SKILL_NAME=collaborative-development-workflow",
        image,
        *runner,
        prompt,
    ]


def _docker_client_environment(home: Path) -> dict[str, str]:
    environment = _safe_inherited_environment()
    environment["PATH"] = os.defpath
    environment["HOME"] = str(home)
    environment["LC_ALL"] = "C"
    return environment


def _docker_runner_client_environment(home: Path) -> dict[str, str]:
    environment = _docker_client_environment(home)
    for name in RUNNER_CONTAINER_CREDENTIAL_KEYS:
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _remove_validation_container(
    container_name: str,
    workspace: Path,
    environment: dict[str, str],
    *,
    settle_seconds: float = 0.0,
    container_id: str | None = None,
) -> None:
    _validate_docker_container_name(container_name)
    if container_id is not None:
        if not isinstance(container_id, str):
            raise EvalError("Docker container ID must be text")
        _validate_case_text(container_id, "Docker container ID")
        try:
            encoded_container_id = container_id.encode("ascii")
        except UnicodeEncodeError as exc:
            raise EvalError("Docker container ID must be ASCII") from exc
        _docker_container_id(encoded_container_id)
    removal_target = container_name if container_id is None else container_id
    cleanup_deadline = time.monotonic() + DOCKER_CLEANUP_TIMEOUT_SECONDS
    absent_since: float | None = None
    last_error: BaseException | None = None
    while True:
        now = time.monotonic()
        remaining_time = cleanup_deadline - now
        if remaining_time <= 0:
            error = _ContainerCleanupUnconfirmed(
                f"cannot confirm cleanup of Docker container {container_name}"
            )
            if last_error is not None:
                raise error from last_error
            raise error
        command_timeout = min(5.0, remaining_time)
        try:
            # Repeated exact-ID/name removal also covers a killed Docker client
            # whose create request was in flight.
            _run(
                ["docker", "container", "rm", "--force", removal_target],
                workspace,
                timeout=command_timeout,
                capture_limit=64 * 1024,
                env=environment,
            )
            remaining = _run(
                [
                    "docker",
                    "container",
                    "ls",
                    "--all",
                    "--filter",
                    f"label={_docker_container_label(container_name)}",
                    "--format",
                    "{{.ID}} {{.Names}}",
                ],
                workspace,
                timeout=command_timeout,
                capture_limit=64 * 1024,
                env=environment,
            )
        except FileNotFoundError as exc:
            raise _ContainerCleanupUnconfirmed(
                "Docker executable is not available for cleanup"
            ) from exc
        except (OSError, EvalError) as exc:
            last_error = exc
            absent_since = None
        else:
            if remaining.returncode == 0 and not remaining.stdout.strip():
                if absent_since is None:
                    absent_since = time.monotonic()
                if time.monotonic() - absent_since >= settle_seconds:
                    return
            else:
                last_error = EvalError("Docker still reports the container")
                absent_since = None
        sleep_for = min(
            DOCKER_CLEANUP_POLL_SECONDS,
            max(0.0, cleanup_deadline - time.monotonic()),
        )
        if sleep_for:
            time.sleep(sleep_for)


def _run_docker_validation(
    command: list[str],
    workspace: Path,
    timeout: int | float,
    image: str,
    client_environment: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    container_name = f"cdw-validation-{secrets.token_hex(16)}"
    container_id: str | None = None
    docker_request_may_be_in_flight = True
    lifecycle_deadline = time.monotonic() + timeout
    try:
        created = _run(
            _docker_validation_argv(image, workspace, command, container_name),
            workspace,
            timeout=timeout,
            capture_limit=64 * 1024,
            env=client_environment,
        )
        docker_request_may_be_in_flight = False
        if created.returncode != 0:
            return created
        container_id = _docker_container_id(created.stdout)
        docker_request_may_be_in_flight = True
        result = _run(
            ["docker", "container", "start", "--attach", container_id],
            workspace,
            timeout=max(0.001, lifecycle_deadline - time.monotonic()),
            env=client_environment,
        )
        docker_request_may_be_in_flight = False
        return result
    finally:
        _remove_validation_container(
            container_name,
            workspace,
            client_environment,
            settle_seconds=(
                DOCKER_CLEANUP_RACE_SECONDS
                if docker_request_may_be_in_flight
                else 0.0
            ),
            container_id=container_id,
        )


def _run_docker_runner(
    runner: list[str],
    prompt: str,
    workspace: Path,
    timeout: int | float,
    image: str,
    client_environment: dict[str, str],
    container_skill_path: str,
) -> subprocess.CompletedProcess[bytes]:
    container_name = f"cdw-runner-{secrets.token_hex(16)}"
    cleanup_environment = {
        name: value
        for name, value in client_environment.items()
        if name not in RUNNER_CONTAINER_CREDENTIAL_KEYS
    }
    container_id: str | None = None
    docker_request_may_be_in_flight = True
    lifecycle_deadline = time.monotonic() + timeout
    try:
        created = _run(
            _docker_runner_argv(
                image,
                workspace,
                runner,
                prompt,
                container_name,
                container_skill_path,
            ),
            workspace,
            timeout=timeout,
            capture_limit=64 * 1024,
            env=client_environment,
        )
        docker_request_may_be_in_flight = False
        if created.returncode != 0:
            return created
        container_id = _docker_container_id(created.stdout)
        docker_request_may_be_in_flight = True
        result = _run(
            ["docker", "container", "start", "--attach", container_id],
            workspace,
            timeout=max(0.001, lifecycle_deadline - time.monotonic()),
            env=cleanup_environment,
        )
        docker_request_may_be_in_flight = False
        return result
    finally:
        _remove_validation_container(
            container_name,
            workspace,
            cleanup_environment,
            settle_seconds=(
                DOCKER_CLEANUP_RACE_SECONDS
                if docker_request_may_be_in_flight
                else 0.0
            ),
            container_id=container_id,
        )


def _unsafe_direct_runner_isolation() -> dict[str, Any]:
    return {
        "mode": "unsafe-direct",
        "os_isolation_boundary": False,
        "pid_namespace": "shared_with_evaluator",
        "network": "inherited",
        "root_filesystem": "host",
        "same_user_filesystem_access": "possible",
        "same_user_process_access": "possible",
        "environment_policy": "inherits_evaluator_environment",
        "detached_descendant_teardown": "not_guaranteed",
    }


def _docker_runner_isolation(image: str, container_skill_path: str) -> dict[str, Any]:
    return {
        "mode": "docker",
        "os_isolation_boundary": True,
        "pid_namespace": "docker_default_private",
        "network_requested": "docker_bridge_unrestricted_egress",
        "api_only_egress_enforced": False,
        "host_lan_link_local_and_peer_access_possible": True,
        "requires_disposable_network_isolated_host": True,
        "root_filesystem_requested": "read_only",
        "capabilities_requested": "all_dropped",
        "no_new_privileges_requested": True,
        "host_bind_mounts_requested": ["workspace"],
        "docker_socket_mounted": False,
        "temporary_filesystems_requested": ["/tmp"],
        "environment_policy": (
            "container_image_defaults_plus_fixed_values_and_openai_api_key"
        ),
        "api_credential_passed_to_runner_container": True,
        "credential_access_by_model_controlled_commands": "possible",
        "command_level_credential_isolation": False,
        "container_lifecycle": "named_create_then_start_attach_then_force_remove",
        "container_id_captured_before_start": True,
        "detached_descendant_teardown": "container_force_removed_and_absence_checked",
        "container_cleanup_race_recheck_seconds": DOCKER_CLEANUP_RACE_SECONDS,
        "daemon_creation_after_cleanup_window": "possible",
        "resource_limits_requested": {
            "cpus": 2,
            "memory_bytes": 2147483648,
            "memory_and_swap_bytes": 2147483648,
            "pids": 512,
            "open_files": 2048,
            "file_size_bytes": MAX_INVENTORY_FILE_BYTES,
        },
        "workspace_host_filesystem_quota": "not_provided",
        "workspace_post_run_limits": {
            "entries": MAX_INVENTORY_ENTRIES,
            "total_bytes": MAX_INVENTORY_TOTAL_BYTES,
        },
        "container_skill_path": container_skill_path,
        "image": image,
    }


def _unsafe_direct_validation_isolation(
    environment_gate: str, unsafe_environment_names: list[str]
) -> dict[str, Any]:
    return {
        "mode": "unsafe-direct",
        "inherited_environment_name_gate": environment_gate,
        "inherited_environment_names_outside_allowlist": unsafe_environment_names,
        "os_isolation_boundary": False,
        "pid_namespace": "shared_with_evaluator",
        "network": "inherited",
        "root_filesystem": "host",
        "same_user_filesystem_access": "possible",
        "same_user_process_access": "possible",
        "capabilities": "inherited",
        "no_new_privileges": False,
        "host_bind_mounts": "not_applicable",
        "environment_policy": "minimal_allowlist",
        "evaluator_environment_access": "possible",
    }


def _docker_validation_isolation(
    image: str, unsafe_environment_names: list[str]
) -> dict[str, Any]:
    return {
        "mode": "docker",
        "inherited_environment_name_gate": "not_applied_in_docker_mode",
        "inherited_environment_names_outside_allowlist": unsafe_environment_names,
        "os_isolation_boundary": True,
        "pid_namespace": "docker_default_private",
        "network_requested": "none",
        "root_filesystem_requested": "read_only",
        "capabilities_requested": "all_dropped",
        "no_new_privileges_requested": True,
        "host_bind_mounts_requested": ["workspace"],
        "docker_socket_mounted": False,
        "temporary_filesystems_requested": ["/tmp"],
        "environment_policy": (
            "empty_then_fixed_minimal; no_host_credentials_passed_directly; "
            "runner_workspace_may_contain_credentials"
        ),
        "credential_environment_passed": False,
        "runner_workspace_credential_transfer_possible": True,
        "evaluator_environment_access": "blocked_by_pid_namespace",
        "container_lifecycle": "named_create_then_start_attach_then_force_remove",
        "container_id_captured_before_start": True,
        "container_cleanup": "force_remove_random_exact_name_then_confirm_absent",
        "container_cleanup_race_recheck_seconds": DOCKER_CLEANUP_RACE_SECONDS,
        "daemon_creation_after_cleanup_window": "possible",
        "resource_limits_requested": {
            "cpus": 2,
            "memory_bytes": 1073741824,
            "memory_and_swap_bytes": 1073741824,
            "pids": 256,
            "open_files": 1024,
            "file_size_bytes": MAX_INVENTORY_FILE_BYTES,
        },
        "workspace_host_filesystem_quota": "not_provided",
        "workspace_post_run_limits": {
            "entries": MAX_INVENTORY_ENTRIES,
            "total_bytes": MAX_INVENTORY_TOTAL_BYTES,
        },
        "image": image,
    }


def _run_validation_commands(
    commands: list[list[str]],
    workspace: Path,
    case_deadline: float,
    validation_isolation: str,
    validation_image: str | None,
) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, Any], bool]:
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    deadline_exhausted = False
    with tempfile.TemporaryDirectory(prefix="cdw-validation-home-") as home_text:
        isolated_home = Path(home_text)
        validation_environment = _minimal_validation_environment(isolated_home)

        if validation_isolation == "unsafe-direct":
            try:
                unsafe_environment_names = _unsafe_inherited_environment_names()
            except EvalError as exc:
                unsafe_environment_names = []
                isolation = _unsafe_direct_validation_isolation(
                    "unverified", unsafe_environment_names
                )
                errors.append(str(exc))
            else:
                if unsafe_environment_names:
                    isolation = _unsafe_direct_validation_isolation(
                        "blocked", unsafe_environment_names
                    )
                    errors.append(
                        "unsafe direct validation refused because the evaluator "
                        "inherited environment variables outside the strict allowlist: "
                        + ", ".join(unsafe_environment_names)
                    )
                else:
                    isolation = _unsafe_direct_validation_isolation(
                        "passed", unsafe_environment_names
                    )
            if errors:
                for command in commands:
                    checks.append(
                        {
                            "argv": command,
                            "exit_code": None,
                            "status": "not_run_unsafe_parent_environment",
                            "output_bytes": 0,
                        }
                    )
                return (
                    checks,
                    errors,
                    unsafe_environment_names,
                    isolation,
                    deadline_exhausted,
                )
            docker_image = None
            docker_environment = None
        else:
            docker_image = _validate_docker_image_id(validation_image)
            docker_environment = _docker_client_environment(isolated_home)
            try:
                unsafe_environment_names = _unsafe_inherited_environment_names()
            except EvalError:
                unsafe_environment_names = _unsafe_inherited_environment_names(
                    set(os.environ)
                )
            isolation = _docker_validation_isolation(
                docker_image, unsafe_environment_names
            )

        for directory in (
            isolated_home / ".codex",
            isolated_home / ".config",
            isolated_home / ".local" / "share",
            isolated_home / ".local" / "state",
            isolated_home / ".cache",
            isolated_home / ".runtime",
            isolated_home / ".tmp",
        ):
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        for command_index, command in enumerate(commands):
            remaining_time = case_deadline - time.monotonic()
            if remaining_time <= 0:
                deadline_exhausted = True
                for skipped in commands[command_index:]:
                    checks.append(
                        {
                            "argv": skipped,
                            "exit_code": None,
                            "status": "not_run_case_timeout",
                            "output_bytes": 0,
                        }
                    )
                errors.append("case deadline expired before all validation commands ran")
                break
            try:
                if validation_isolation == "docker":
                    assert docker_image is not None and docker_environment is not None
                    check = _run_docker_validation(
                        command,
                        workspace,
                        remaining_time,
                        docker_image,
                        docker_environment,
                    )
                else:
                    check = _run(
                        command,
                        workspace,
                        timeout=remaining_time,
                        env=validation_environment.copy(),
                    )
            except _ProcessTimedOut as exc:
                deadline_exhausted = True
                checks.append(
                    {
                        "argv": command,
                        "exit_code": None,
                        "status": "case_timeout",
                        "output_bytes": len(exc.stdout) + len(exc.stderr),
                    }
                )
                for skipped in commands[command_index + 1 :]:
                    checks.append(
                        {
                            "argv": skipped,
                            "exit_code": None,
                            "status": "not_run_case_timeout",
                            "output_bytes": 0,
                        }
                    )
                errors.append(
                    f"case deadline expired during validation command: {command!r}"
                )
                break
            except _ProcessOutputLimit as exc:
                checks.append(
                    {
                        "argv": command,
                        "exit_code": None,
                        "status": "output_limit",
                        "output_bytes": len(exc.stdout) + len(exc.stderr),
                    }
                )
                errors.append(
                    f"validation command output exceeded the limit: {command!r}"
                )
            else:
                checks.append(
                    {
                        "argv": command,
                        "exit_code": check.returncode,
                        "status": "completed",
                        "output_bytes": len(check.stdout) + len(check.stderr),
                        "output_sha256": hashlib.sha256(
                            _combined_output(check.stdout, check.stderr)
                        ).hexdigest(),
                    }
                )
                if check.returncode != 0:
                    errors.append(
                        f"validation command exited {check.returncode}: {command!r}"
                    )
    return checks, errors, unsafe_environment_names, isolation, deadline_exhausted


def run_case(
    case_path: Path,
    source: Path,
    skill_path: Path,
    runner: list[str],
    workspace: Path,
    validation_isolation: str = "docker",
    validation_image: str | None = None,
    runner_isolation: str = "docker",
    runner_image: str | None = None,
    runner_tool_version: str | None = None,
) -> tuple[int, dict[str, Any]]:
    _validate_path_text(case_path, "behavior case path")
    _validate_path_text(source, "source root path")
    _validate_path_text(skill_path, "Skill path")
    _validate_path_text(workspace, "workspace path")
    case = _load_case(case_path)
    case_deadline = time.monotonic() + case["timeout_seconds"]
    source = source.resolve()
    skill_path = skill_path.resolve()
    workspace = workspace.resolve(strict=False)
    if not source.is_dir():
        raise EvalError(f"source root is not a directory: {source}")
    if not skill_path.joinpath("SKILL.md").is_file():
        raise EvalError(f"Skill path has no SKILL.md: {skill_path}")
    if not runner or not all(isinstance(item, str) and item for item in runner):
        raise EvalError("runner must contain nonempty arguments")
    for item in runner:
        _validate_case_text(item, "runner argument")
    if runner_isolation not in RUNNER_ISOLATIONS:
        raise EvalError(
            "runner isolation must be one of: "
            + ", ".join(sorted(RUNNER_ISOLATIONS))
        )
    if runner_isolation == "docker":
        docker_runner_image = _validate_docker_image_id(runner_image)
        observed_runner_tool_version = _validate_runner_tool_version(
            runner_tool_version
        )
        runner_api_key = os.environ.get("OPENAI_API_KEY")
        if not runner_api_key:
            raise EvalError(
                "Docker runner isolation requires a nonempty OPENAI_API_KEY"
            )
        _validate_case_text(runner_api_key, "OPENAI_API_KEY")
    elif runner_image is not None or runner_tool_version is not None:
        raise EvalError(
            "--runner-image and --runner-tool-version are valid only with "
            "Docker runner isolation"
        )
    else:
        docker_runner_image = None
        observed_runner_tool_version = None
    if validation_isolation not in VALIDATION_ISOLATIONS:
        raise EvalError(
            "validation isolation must be one of: "
            + ", ".join(sorted(VALIDATION_ISOLATIONS))
        )
    if validation_isolation == "docker":
        docker_validation_image = _validate_docker_image_id(validation_image)
    elif validation_image is not None:
        raise EvalError("--validation-image is valid only with docker isolation")
    else:
        docker_validation_image = None

    relative_skill_path: Path | None = None
    try:
        relative_skill_path = skill_path.relative_to(source)
    except ValueError:
        if runner_isolation == "docker":
            raise EvalError("Docker runner requires the Skill path to be inside source")

    _prepare_workspace(source, workspace, case_deadline)
    workspace_identity = _workspace_root_identity(workspace)
    git_directory_identity = _git_directory_identity(workspace)
    if runner_isolation == "docker":
        assert relative_skill_path is not None
        container_skill_path = (Path("/workspace") / relative_skill_path).as_posix()
        effective_skill_path = Path(container_skill_path)
        assert docker_runner_image is not None
        runner_isolation_report = _docker_runner_isolation(
            docker_runner_image, container_skill_path
        )
    else:
        container_skill_path = None
        effective_skill_path = (
            skill_path
            if relative_skill_path is None
            else workspace / relative_skill_path
        )
        runner_isolation_report = _unsafe_direct_runner_isolation()
    prompt = case["prompt"].replace("{SKILL_PATH}", str(effective_skill_path))
    _validate_case_text(prompt, "runner prompt")

    baseline_workspace_usage = _enforce_workspace_budget(
        workspace, case_deadline, "baseline workspace budget check"
    )
    tracked_cache_paths = frozenset(
        relative.as_posix()
        for relative, _mode in _tracked_regular_files(workspace, case_deadline)
        if _is_cache_artifact(relative)
    )
    baseline_inventory = _filesystem_inventory(
        workspace,
        exclude_git=True,
        exclude_caches=True,
        included_cache_paths=tracked_cache_paths,
        deadline=case_deadline,
        deadline_phase="baseline inventory",
    )
    baseline_repository = _repository_state(
        workspace, case_deadline, "baseline repository inspection"
    )
    result: subprocess.CompletedProcess[bytes] | None = None
    runner_stop: _ProcessStopped | None = None
    try:
        if runner_isolation == "docker":
            assert docker_runner_image is not None and container_skill_path is not None
            with tempfile.TemporaryDirectory(prefix="cdw-runner-client-home-") as home:
                result = _run_docker_runner(
                    runner,
                    prompt,
                    workspace,
                    max(0.001, case_deadline - time.monotonic()),
                    docker_runner_image,
                    _docker_runner_client_environment(Path(home)),
                    container_skill_path,
                )
        else:
            runner_environment = os.environ.copy()
            # Read-only Git commands such as status may otherwise refresh raw
            # index metadata. Explicit writes such as git add still take a lock.
            runner_environment["GIT_OPTIONAL_LOCKS"] = "0"
            result = _run(
                runner + [prompt],
                workspace,
                timeout=max(0.001, case_deadline - time.monotonic()),
                env=runner_environment,
            )
    except _ProcessStopped as exc:
        runner_stop = exc

    _verify_workspace_root(workspace, workspace_identity, "during runner execution")
    _verify_git_directory(
        workspace, git_directory_identity, "during runner execution"
    )
    after_runner_workspace_usage = _inspect_runner_controlled_workspace(
        "post-run workspace budget check",
        _enforce_workspace_budget,
        workspace,
        case_deadline,
        "post-run workspace budget check",
    )

    after_runner_inventory = _inspect_runner_controlled_workspace(
        "post-run inventory",
        _filesystem_inventory,
        workspace,
        exclude_git=True,
        exclude_caches=True,
        included_cache_paths=tracked_cache_paths,
        deadline=case_deadline,
        deadline_phase="post-run inventory",
    )
    after_runner_repository = _inspect_runner_controlled_workspace(
        "post-run repository inspection",
        _repository_state,
        workspace,
        case_deadline,
        "post-run repository inspection",
    )
    errors: list[str] = []
    if isinstance(runner_stop, _ProcessTimedOut):
        errors.append("runner timed out")
    elif isinstance(runner_stop, _ProcessOutputLimit):
        errors.append("runner output exceeded the evaluation limit")
    elif result is not None and result.returncode != 0:
        errors.append(f"runner exited {result.returncode}")

    stdout = result.stdout if result is not None else runner_stop.stdout if runner_stop else b""
    stderr = result.stderr if result is not None else runner_stop.stderr if runner_stop else b""
    combined = _combined_output(stdout, stderr)
    output_text = combined.decode("utf-8", "replace")
    output_lines = {line.strip() for line in output_text.splitlines()}
    output_requirements: list[dict[str, Any]] = []
    for expected in case["required_output_lines"]:
        present = expected in output_lines
        output_requirements.append(
            {
                "text": expected,
                "present": present,
                "meaning": (
                    "exact stripped runner-output line; runner-claimed text only, "
                    "not independent acceptance evidence"
                ),
            }
        )
        if not present:
            errors.append(f"runner output did not contain exact line {expected!r}")

    _verify_workspace_root(workspace, workspace_identity, "before validation")
    _verify_git_directory(workspace, git_directory_identity, "before validation")

    (
        checks,
        validation_errors,
        validation_unsafe_environment_names,
        validation_isolation_report,
        validation_timeout,
    ) = (
        _run_validation_commands(
            case["validation_commands"],
            workspace,
            case_deadline,
            validation_isolation,
            docker_validation_image,
        )
    )
    errors.extend(validation_errors)
    deadline_exhausted = (
        isinstance(runner_stop, _ProcessTimedOut) or validation_timeout
    )

    _verify_workspace_root(workspace, workspace_identity, "during validation")
    _verify_git_directory(workspace, git_directory_identity, "during validation")
    final_workspace_usage = _inspect_runner_controlled_workspace(
        "final workspace budget check",
        _enforce_workspace_budget,
        workspace,
        case_deadline,
        "final workspace budget check",
    )

    final_inventory = _inspect_runner_controlled_workspace(
        "final inventory",
        _filesystem_inventory,
        workspace,
        exclude_git=True,
        exclude_caches=True,
        included_cache_paths=tracked_cache_paths,
        deadline=case_deadline,
        deadline_phase="final inventory",
    )
    final_repository = _inspect_runner_controlled_workspace(
        "final repository inspection",
        _repository_state,
        workspace,
        case_deadline,
        "final repository inspection",
    )
    changed_after_runner = _inventory_changes(
        baseline_inventory, after_runner_inventory
    )
    meaningful_changed_after_runner = _meaningful_inventory_changes(
        baseline_inventory, after_runner_inventory
    )
    changed_during_validation = _inventory_changes(
        after_runner_inventory, final_inventory
    )
    observed_changed = changed_after_runner | changed_during_validation
    allowed = set(case["allowed_changed_paths"])
    required = set(case["required_changed_paths"])
    unexpected = sorted(changed_after_runner - allowed)
    missing = sorted(required - meaningful_changed_after_runner)
    unexpected_ids = [_opaque_path_identifier(path) for path in unexpected]
    validation_changed_ids = [
        _opaque_path_identifier(path) for path in sorted(changed_during_validation)
    ]
    if unexpected:
        errors.append(
            f"unexpected changed paths ({len(unexpected)}); opaque IDs: "
            + ", ".join(unexpected_ids)
        )
    if missing:
        errors.append("required paths were not changed: " + ", ".join(missing))
    if changed_during_validation:
        errors.append(
            f"validation changed workspace paths ({len(changed_during_validation)}); "
            "opaque IDs: "
            + ", ".join(validation_changed_ids)
        )

    repository_checks = _repository_checks(
        baseline_repository, after_runner_repository, final_repository
    )
    repository_error_names = {
        "head_unchanged": "Git HEAD changed",
        "head_ref_unchanged": "Git HEAD reference changed",
        "refs_unchanged": "Git refs changed",
        "index_unchanged": "Git index changed",
        "git_metadata_unchanged": "Git control metadata changed",
    }
    for check_name, passed in repository_checks.items():
        if not passed:
            errors.append(repository_error_names[check_name])

    report = {
        "version": "skill-behavior-report-v1",
        "case_id": case["id"],
        "valid": not errors,
        "result_meaning": (
            "valid means the fixed evaluator checks and configured runner-text checks "
            "passed; runner text is not independent proof of formal acceptance"
        ),
        "errors": errors,
        "workspace": str(workspace),
        "changed_paths": _reported_paths(
            meaningful_changed_after_runner, allowed
        ),
        "observed_changed_paths": _reported_paths(
            observed_changed, allowed, changed_during_validation
        ),
        "unexpected_changed_path_count": len(unexpected),
        "unexpected_changed_path_ids": unexpected_ids,
        "validation_changed_path_count": len(changed_during_validation),
        "validation_changed_path_ids": validation_changed_ids,
        "workspace_inventory": {
            "baseline_sha256": _inventory_digest(baseline_inventory),
            "after_runner_sha256": _inventory_digest(after_runner_inventory),
            "final_sha256": _inventory_digest(final_inventory),
            "excluded_cache_directories": sorted(CACHE_DIRECTORY_NAMES),
            "excluded_cache_files": sorted(CACHE_FILE_NAMES) + ["*.pyc"],
            "included_tracked_cache_paths": sorted(tracked_cache_paths),
            "resource_usage": {
                "baseline": baseline_workspace_usage,
                "after_runner": after_runner_workspace_usage,
                "final": final_workspace_usage,
            },
            "host_filesystem_quota": "not_provided",
        },
        "repository_integrity": repository_checks,
        "case_deadline_exhausted": deadline_exhausted,
        "runner_exit_code": None if result is None else result.returncode,
        "runner_termination": (
            "timeout"
            if isinstance(runner_stop, _ProcessTimedOut)
            else "output_limit"
            if isinstance(runner_stop, _ProcessOutputLimit)
            else "completed"
        ),
        "runner_output_bytes": len(stdout) + len(stderr),
        "runner_output_sha256": hashlib.sha256(combined).hexdigest(),
        "runner_isolation": runner_isolation_report,
        "container_image_provenance": {
            "runner_local_image_id": docker_runner_image,
            "validation_local_image_id": docker_validation_image,
            "runner_tool_version_observed_before_secret_step": (
                observed_runner_tool_version
            ),
            "runner_tool_version_observation_source": (
                "caller_supplied_pre_secret_image_smoke_check"
                if observed_runner_tool_version is not None
                else None
            ),
            "base_image_digest_pinning": "not_independently_verified_by_evaluator",
        },
        "runner_claimed_line_checks": output_requirements,
        "validation_inherited_environment_names_outside_allowlist": (
            validation_unsafe_environment_names
        ),
        "validation_inherited_environment_allowlist": sorted(
            SAFE_VALIDATION_ENVIRONMENT_KEYS
        ),
        "validation_home_isolated": True,
        "validation_isolation": validation_isolation_report,
        "validation_checks": checks,
    }
    return (0 if report["valid"] else 1), report


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validated_report_path(
    path: Path,
    source: Path,
    workspace: Path | None,
    *,
    label: str = "report",
) -> Path:
    _validate_path_text(path, f"{label} path")
    _validate_path_text(source, "source root path")
    if workspace is not None:
        _validate_path_text(workspace, "workspace path")
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.name:
        raise EvalError(f"{label} path must name a file")
    try:
        parent = absolute.parent.resolve(strict=True)
    except OSError as exc:
        raise EvalError(f"cannot resolve {label} directory: {exc}") from exc
    candidate = parent / absolute.name
    protected_roots = [("source", source.resolve(strict=False))]
    if workspace is not None:
        protected_roots.append(("workspace", workspace.resolve(strict=False)))
    for protected_label, root in protected_roots:
        if _is_within(candidate, root):
            raise EvalError(
                f"{label} path must be outside the evaluator {protected_label}"
            )
    return absolute


def _open_directory_without_symlinks(path: Path) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise EvalError("safe report publication requires O_NOFOLLOW support")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(
                component, flags | no_follow, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _publish_report(path: Path, rendered: str, *, label: str = "report") -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.name:
        raise EvalError(f"{label} path must name a file")
    try:
        directory_descriptor = _open_directory_without_symlinks(absolute.parent)
    except OSError as exc:
        raise EvalError(f"cannot safely open {label} directory: {exc}") from exc

    temporary_name: str | None = None
    file_descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        for _attempt in range(16):
            candidate = f".cdw-report-{secrets.token_hex(16)}.tmp"
            try:
                file_descriptor = os.open(
                    candidate, flags, 0o600, dir_fd=directory_descriptor
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if file_descriptor is None or temporary_name is None:
            raise EvalError(f"cannot reserve a temporary {label} file")

        remaining = memoryview(rendered.encode("utf-8"))
        while remaining:
            written = os.write(file_descriptor, remaining)
            if written <= 0:
                raise OSError("report write made no progress")
            remaining = remaining[written:]
        os.fsync(file_descriptor)
        os.close(file_descriptor)
        file_descriptor = None

        try:
            os.link(
                temporary_name,
                absolute.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise EvalError(f"{label} path already exists: {absolute}") from exc
        os.unlink(temporary_name, dir_fd=directory_descriptor)
        temporary_name = None
    except EvalError:
        raise
    except OSError as exc:
        raise EvalError(f"cannot publish {label} safely: {exc}") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        os.close(directory_descriptor)


def _cleanup_temporary_evaluation_root(
    root: Path, expected_identity: tuple[int, int]
) -> None:
    if _directory_identity(root, "temporary evaluation root") != expected_identity:
        raise EvalError("temporary evaluation root changed before cleanup")
    cleanup_program = r"""
import os
import resource
import stat
import sys

(
    root,
    expected_device,
    expected_inode,
    requested_memory,
    requested_cpu,
    requested_open_files,
    maximum_entries,
    maximum_depth,
) = sys.argv[1:]
expected = (int(expected_device), int(expected_inode))
maximum_entries = int(maximum_entries)
maximum_depth = int(maximum_depth)
directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def apply_limit(kind, requested):
    _soft, hard = resource.getrlimit(kind)
    requested = int(requested)
    effective = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
    resource.setrlimit(kind, (effective, effective))
    return effective


apply_limit(resource.RLIMIT_AS, requested_memory)
apply_limit(resource.RLIMIT_CPU, requested_cpu)
open_file_limit = apply_limit(resource.RLIMIT_NOFILE, requested_open_files)


def same_entry(left, right):
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
    )


def make_directory_accessible(name, status, parent_descriptor):
    os.chmod(
        name,
        stat.S_IMODE(status.st_mode) | stat.S_IRWXU,
        dir_fd=parent_descriptor,
    )
    after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(after.st_mode) or not same_entry(status, after):
        raise RuntimeError("cleanup directory changed while making it accessible")


def open_relative(root_descriptor, components):
    descriptor = os.dup(root_descriptor)
    try:
        for name in components:
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise RuntimeError("cleanup path component changed type")
            make_directory_accessible(name, before, descriptor)
            child = os.open(name, directory_flags, dir_fd=descriptor)
            if not same_entry(before, os.fstat(child)):
                os.close(child)
                raise RuntimeError("cleanup path component changed while opening")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def clear_tree(root_descriptor):
    # Scan entries as a stream, and keep a bounded descriptor window. At a
    # window boundary, reopen from the pinned root by descriptor-relative
    # components. This avoids recursion, unbounded listdir memory, and one fd
    # for every level of an attacker-created tree.
    max_open_frames = max(8, min(128, (open_file_limit - 32) // 2))
    reopen_frame_count = max_open_frames // 2
    components = []
    frames = []
    seen_entries = 0

    def append_frame(descriptor):
        frames.append((descriptor, os.scandir(descriptor)))

    def close_frames():
        while frames:
            descriptor, entries = frames.pop()
            entries.close()
            os.close(descriptor)

    def reset_frames():
        close_frames()
        base_depth = max(0, len(components) - reopen_frame_count + 1)
        append_frame(
            open_relative(root_descriptor, components[:base_depth])
        )
        for name in components[base_depth:]:
            parent_descriptor = frames[-1][0]
            before = os.stat(
                name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            make_directory_accessible(name, before, parent_descriptor)
            child = os.open(
                name, directory_flags, dir_fd=parent_descriptor
            )
            if not same_entry(before, os.fstat(child)):
                os.close(child)
                raise RuntimeError(
                    "cleanup path component changed while reopening"
                )
            append_frame(child)

    reset_frames()
    try:
        while frames:
            descriptor, entries = frames[-1]
            try:
                entry = next(entries)
            except StopIteration:
                entries.close()
                os.close(descriptor)
                frames.pop()
                if not components:
                    return
                child_name = components[-1]
                if frames:
                    parent_descriptor = frames[-1][0]
                    os.rmdir(child_name, dir_fd=parent_descriptor)
                    components.pop()
                else:
                    parent_descriptor = open_relative(
                        root_descriptor, components[:-1]
                    )
                    try:
                        os.rmdir(child_name, dir_fd=parent_descriptor)
                    finally:
                        os.close(parent_descriptor)
                    components.pop()
                    reset_frames()
                continue

            name = entry.name
            try:
                before = os.stat(
                    name, dir_fd=descriptor, follow_symlinks=False
                )
            except FileNotFoundError:
                # A freshly reset ancestor iterator can retain the name of the
                # child that this cleanup pass just removed.
                continue
            seen_entries += 1
            if seen_entries > maximum_entries:
                raise RuntimeError("cleanup traversal exceeds the entry limit")
            if not stat.S_ISDIR(before.st_mode):
                os.unlink(name, dir_fd=descriptor)
                continue
            make_directory_accessible(name, before, descriptor)
            child = os.open(name, directory_flags, dir_fd=descriptor)
            if not same_entry(before, os.fstat(child)):
                os.close(child)
                raise RuntimeError("cleanup directory changed while opening")
            if len(components) >= maximum_depth:
                os.close(child)
                raise RuntimeError("cleanup traversal exceeds the depth limit")
            components.append(name)
            append_frame(child)
            if len(frames) >= max_open_frames:
                close_frames()
                append_frame(open_relative(root_descriptor, components))
    finally:
        close_frames()


parent = os.path.dirname(root)
root_name = os.path.basename(root)
parent_descriptor = os.open(os.path.sep, directory_flags)
try:
    for component in parent.split(os.path.sep):
        if not component:
            continue
        next_descriptor = os.open(
            component, directory_flags, dir_fd=parent_descriptor
        )
        os.close(parent_descriptor)
        parent_descriptor = next_descriptor
    root_before = os.stat(
        root_name, dir_fd=parent_descriptor, follow_symlinks=False
    )
    if (
        not stat.S_ISDIR(root_before.st_mode)
        or (root_before.st_dev, root_before.st_ino) != expected
    ):
        raise RuntimeError("cleanup root identity changed")
    make_directory_accessible(root_name, root_before, parent_descriptor)
    root_descriptor = os.open(
        root_name, directory_flags, dir_fd=parent_descriptor
    )
    try:
        if not same_entry(root_before, os.fstat(root_descriptor)):
            raise RuntimeError("cleanup root changed while opening")
        clear_tree(root_descriptor)
    finally:
        os.close(root_descriptor)
    os.rmdir(root_name, dir_fd=parent_descriptor)
finally:
    os.close(parent_descriptor)
"""
    environment = {
        "HOME": str(root.parent),
        "LC_ALL": "C",
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        result = _run(
            [
                os.path.realpath(sys.executable),
                "-I",
                "-c",
                cleanup_program,
                str(root),
                str(expected_identity[0]),
                str(expected_identity[1]),
                str(TEMPORARY_CLEANUP_MEMORY_BYTES),
                str(TEMPORARY_CLEANUP_CPU_SECONDS),
                str(TEMPORARY_CLEANUP_OPEN_FILES),
                str(TEMPORARY_CLEANUP_MAX_ENTRIES),
                str(TEMPORARY_CLEANUP_MAX_DEPTH),
            ],
            root.parent,
            timeout=TEMPORARY_CLEANUP_TIMEOUT_SECONDS,
            capture_limit=64 * 1024,
            env=environment,
        )
    except _ProcessTimedOut as exc:
        raise EvalError("temporary workspace cleanup timed out") from exc
    except (_ProcessOutputLimit, OSError) as exc:
        raise EvalError("temporary workspace cleanup failed") from exc
    if result.returncode != 0 or os.path.lexists(root):
        raise EvalError("temporary workspace cleanup failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--source-root", default=".", type=Path)
    parser.add_argument("--skill-path", default=".", type=Path)
    parser.add_argument(
        "--runner-json",
        required=True,
        help='JSON argument list, for example ["codex", "exec", "--ephemeral"]',
    )
    parser.add_argument(
        "--runner-isolation",
        choices=sorted(RUNNER_ISOLATIONS),
        default="docker",
        help=(
            "run the runner in Docker (default), or explicitly choose unsafe-direct "
            "for unisolated host execution"
        ),
    )
    parser.add_argument(
        "--runner-image",
        help="immutable local sha256 image ID used with Docker runner isolation",
    )
    parser.add_argument(
        "--runner-tool-version",
        help="tool version observed from the runner image before exposing secrets",
    )
    parser.add_argument(
        "--validation-isolation",
        choices=sorted(VALIDATION_ISOLATIONS),
        default="docker",
        help=(
            "run validation in Docker (default), or explicitly choose unsafe-direct "
            "for unisolated host execution"
        ),
    )
    parser.add_argument(
        "--validation-image",
        help="immutable local sha256 image ID used with Docker validation isolation",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "caller-managed workspace; required whenever runner or validation "
            "uses unsafe-direct isolation"
        ),
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--publication-marker",
        type=Path,
        help=(
            "exclusive marker containing the published report SHA-256; requires "
            "--report and is intended to gate artifact upload"
        ),
    )
    args = parser.parse_args(argv)
    report_path: Path | None = None
    publication_marker_path: Path | None = None
    temporary_root: Path | None = None
    temporary_root_identity: tuple[int, int] | None = None
    automatic_cleanup_allowed = True
    try:
        runner = json.loads(args.runner_json)
        if not isinstance(runner, list) or not all(
            isinstance(item, str) and item for item in runner
        ):
            raise EvalError("--runner-json must be a JSON list of strings")
        for item in runner:
            _validate_case_text(item, "runner argument")
        source = args.source_root.resolve()
        skill_path = args.skill_path.resolve()
        requested_workspace = (
            None if args.workspace is None else args.workspace.resolve(strict=False)
        )
        if args.report is not None:
            report_path = _validated_report_path(
                args.report, source, requested_workspace
            )
        if args.publication_marker is not None:
            if report_path is None:
                raise EvalError("--publication-marker requires --report")
            publication_marker_path = _validated_report_path(
                args.publication_marker,
                source,
                requested_workspace,
                label="publication marker",
            )
            if publication_marker_path == report_path:
                raise EvalError("publication marker and report paths must differ")
        if requested_workspace is None and (
            args.runner_isolation == "unsafe-direct"
            or args.validation_isolation == "unsafe-direct"
        ):
            raise EvalError(
                "--workspace is required when runner or validation isolation is "
                "unsafe-direct; automatic temporary cleanup is Docker-only"
            )
        if args.workspace is None:
            temporary_root = Path(tempfile.mkdtemp(prefix="cdw-behavior-"))
            temporary_root_identity = _directory_identity(
                temporary_root, "temporary evaluation root"
            )
            workspace = temporary_root / "workspace"
            if report_path is not None:
                _validated_report_path(report_path, source, workspace)
            if publication_marker_path is not None:
                _validated_report_path(
                    publication_marker_path,
                    source,
                    workspace,
                    label="publication marker",
                )
            code, report = run_case(
                args.case,
                source,
                skill_path,
                runner,
                workspace,
                args.validation_isolation,
                args.validation_image,
                args.runner_isolation,
                args.runner_image,
                args.runner_tool_version,
            )
        else:
            assert requested_workspace is not None
            code, report = run_case(
                args.case,
                source,
                skill_path,
                runner,
                requested_workspace,
                args.validation_isolation,
                args.validation_image,
                args.runner_isolation,
                args.runner_image,
                args.runner_tool_version,
            )
    except _ContainerCleanupUnconfirmed as exc:
        automatic_cleanup_allowed = False
        code = 2
        report = {
            "version": "skill-behavior-report-v1",
            "valid": False,
            "result_meaning": "the evaluator could not complete its fixed checks",
            "errors": [str(exc)],
        }
    except (ValueError, OSError) as exc:
        code = 2
        report = {
            "version": "skill-behavior-report-v1",
            "valid": False,
            "result_meaning": "the evaluator could not complete its fixed checks",
            "errors": [str(exc)],
        }
    if temporary_root is not None:
        cleanup_error: str | None = None
        if automatic_cleanup_allowed:
            try:
                if temporary_root_identity is None:
                    raise EvalError("temporary evaluation root was not safely pinned")
                _cleanup_temporary_evaluation_root(
                    temporary_root, temporary_root_identity
                )
            except (ValueError, OSError) as exc:
                cleanup_error = str(exc)
                code = 2
                report["valid"] = False
                report.setdefault("errors", []).append(cleanup_error)
            report["temporary_workspace_cleanup"] = {
                "status": "failed" if cleanup_error is not None else "completed",
                "manual_cleanup_required": cleanup_error is not None,
                "timeout_seconds": TEMPORARY_CLEANUP_TIMEOUT_SECONDS,
                "outside_case_deadline": True,
                "target_policy": "exact_generated_root_with_inode_recheck",
                "automatic_cleanup_scope": (
                    "docker_isolation_only_after_container_teardown"
                ),
                "untrusted_processes_expected_stopped_before_cleanup": True,
                "worker_resource_limits_requested": {
                    "memory_bytes": TEMPORARY_CLEANUP_MEMORY_BYTES,
                    "cpu_seconds": TEMPORARY_CLEANUP_CPU_SECONDS,
                    "open_files": TEMPORARY_CLEANUP_OPEN_FILES,
                    "traversal_entries": TEMPORARY_CLEANUP_MAX_ENTRIES,
                    "depth": TEMPORARY_CLEANUP_MAX_DEPTH,
                },
            }
        else:
            report["temporary_workspace_cleanup"] = {
                "status": "not_performed_container_teardown_unconfirmed",
                "outside_case_deadline": True,
                "automatic_cleanup_scope": (
                    "docker_isolation_only_after_confirmed_container_teardown"
                ),
                "manual_cleanup_required": True,
            }
    else:
        report["temporary_workspace_cleanup"] = {
            "status": "not_performed_for_caller_managed_workspace",
            "outside_case_deadline": True,
        }
    rendered = json.dumps(report, ensure_ascii=True, sort_keys=True) + "\n"
    if report_path is not None:
        try:
            _publish_report(report_path, rendered)
            if publication_marker_path is not None:
                report_digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
                _publish_report(
                    publication_marker_path,
                    report_digest + "\n",
                    label="publication marker",
                )
        except (ValueError, OSError) as exc:
            code = 2
            report = {
                "version": "skill-behavior-report-v1",
                "valid": False,
                "result_meaning": "the evaluator could not publish its report safely",
                "errors": [str(exc)],
            }
            rendered = json.dumps(report, ensure_ascii=True, sort_keys=True) + "\n"
    print(rendered, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
