#!/usr/bin/env python3
"""Bounded, non-following reads for files in an untrusted checkout."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import Iterable


READ_CHUNK_BYTES = 64 * 1024


class SafeReadError(ValueError):
    """A requested path cannot be read safely within the checkout."""


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


def _file_flags() -> int:
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _stable_file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _absolute_directory(path: str | Path) -> tuple[Path, int]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if os.name != "posix" or not all(
        hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    ):
        raise SafeReadError("safe checkout reads require POSIX descriptor operations")

    descriptor = os.open(os.path.sep, _directory_flags())
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, _directory_flags(), dir_fd=descriptor)
            try:
                info = os.fstat(next_descriptor)
                bound = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) or not _same_file(info, bound):
                    raise SafeReadError("checkout directory changed while it was opened")
            except BaseException:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        return absolute, descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _relative_parts(relative: str | PurePosixPath) -> tuple[str, ...]:
    raw = str(relative)
    if not raw or "\x00" in raw or "\\" in raw:
        raise SafeReadError("checkout path must be a nonempty POSIX relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SafeReadError("checkout path must stay below the checkout root")
    return path.parts


class SafeRoot:
    """Hold one checkout root inode and perform bounded operations below it."""

    def __init__(self, root: str | Path):
        self.path, self._descriptor = _absolute_directory(root)

    def __enter__(self) -> "SafeRoot":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._descriptor >= 0:
            os.close(self._descriptor)
            self._descriptor = -1

    def _require_open(self) -> int:
        if self._descriptor < 0:
            raise SafeReadError("checkout reader is closed")
        return self._descriptor

    def _assert_root_binding(self) -> None:
        expected = os.fstat(self._require_open())
        _path, actual_descriptor = _absolute_directory(self.path)
        try:
            if not _same_file(expected, os.fstat(actual_descriptor)):
                raise SafeReadError("checkout root changed during validation")
        finally:
            os.close(actual_descriptor)

    def assert_bound(self) -> None:
        """Fail if the checkout path no longer names the held root directory."""
        self._assert_root_binding()

    def _open_parent(self, relative: str | PurePosixPath) -> tuple[int, str]:
        parts = _relative_parts(relative)
        descriptor = os.dup(self._require_open())
        try:
            for component in parts[:-1]:
                next_descriptor = os.open(
                    component, _directory_flags(), dir_fd=descriptor
                )
                try:
                    info = os.fstat(next_descriptor)
                    bound = os.stat(
                        component, dir_fd=descriptor, follow_symlinks=False
                    )
                    if not stat.S_ISDIR(info.st_mode) or not _same_file(info, bound):
                        raise SafeReadError(
                            "checkout directory changed while it was opened"
                        )
                except BaseException:
                    os.close(next_descriptor)
                    raise
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor, parts[-1]
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _read_at(
        parent_descriptor: int,
        name: str,
        label: str,
        max_bytes: int,
    ) -> bytes:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
            raise SafeReadError("file byte limit must be a non-negative integer")
        try:
            descriptor = os.open(name, _file_flags(), dir_fd=parent_descriptor)
        except OSError as exc:
            raise SafeReadError(f"cannot safely open {label}: {exc}") from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise SafeReadError(f"{label} must be a regular file")
            if before.st_size > max_bytes:
                raise SafeReadError(f"{label} exceeds the {max_bytes}-byte limit")

            chunks: list[bytes] = []
            total = 0
            while total <= max_bytes:
                chunk = os.read(
                    descriptor,
                    min(READ_CHUNK_BYTES, max_bytes + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > max_bytes:
                raise SafeReadError(f"{label} exceeds the {max_bytes}-byte limit")

            after = os.fstat(descriptor)
            if _stable_file_identity(before) != _stable_file_identity(after):
                raise SafeReadError(f"{label} changed while it was read")
            bound = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            if not _same_file(after, bound):
                raise SafeReadError(f"{label} changed while it was read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def read_bytes(self, relative: str | PurePosixPath, max_bytes: int) -> bytes:
        parent_descriptor, name = self._open_parent(relative)
        try:
            value = self._read_at(parent_descriptor, name, str(relative), max_bytes)
            self._assert_root_binding()
            return value
        finally:
            os.close(parent_descriptor)

    def read_text(self, relative: str | PurePosixPath, max_bytes: int) -> str:
        try:
            return self.read_bytes(relative, max_bytes).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SafeReadError(f"{relative} is not valid UTF-8") from exc

    def is_regular_file(self, relative: str | PurePosixPath) -> bool:
        try:
            parent_descriptor, name = self._open_parent(relative)
        except (OSError, SafeReadError):
            return False
        try:
            try:
                info = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except OSError:
                return False
            return stat.S_ISREG(info.st_mode)
        finally:
            os.close(parent_descriptor)

    def path_exists(self, relative: str | PurePosixPath) -> bool:
        try:
            parent_descriptor, name = self._open_parent(relative)
        except (OSError, SafeReadError):
            return False
        try:
            try:
                info = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except OSError:
                return False
            return not stat.S_ISLNK(info.st_mode)
        finally:
            os.close(parent_descriptor)

    def scan_text_files(
        self,
        *,
        suffix: str,
        skip_top_level: Iterable[str] = (),
        max_depth: int,
        max_entries: int,
        max_files: int,
        max_file_bytes: int,
        max_total_bytes: int,
    ) -> list[tuple[PurePosixPath, str]]:
        """Read matching regular files without following directory or file links."""
        if not suffix:
            raise SafeReadError("scan suffix must be nonempty")
        skip = set(skip_top_level)
        documents: list[tuple[PurePosixPath, str]] = []
        entry_count = 0
        total_bytes = 0

        def walk(descriptor: int, parents: tuple[str, ...]) -> None:
            nonlocal entry_count, total_bytes
            if len(parents) > max_depth:
                raise SafeReadError(
                    f"documentation tree exceeds the {max_depth}-component depth limit"
                )
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > max_entries:
                        raise SafeReadError(
                            f"checkout exceeds the {max_entries}-entry scan limit"
                        )
                    if not parents and entry.name in skip:
                        continue
                    relative = PurePosixPath(*parents, entry.name)
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise SafeReadError(f"cannot inspect {relative}: {exc}") from exc
                    if stat.S_ISDIR(info.st_mode):
                        try:
                            child = os.open(
                                entry.name, _directory_flags(), dir_fd=descriptor
                            )
                        except OSError as exc:
                            raise SafeReadError(
                                f"cannot safely open directory {relative}: {exc}"
                            ) from exc
                        try:
                            if not _same_file(info, os.fstat(child)):
                                raise SafeReadError(
                                    f"directory {relative} changed during validation"
                                )
                            walk(child, (*parents, entry.name))
                            rebound = os.stat(
                                entry.name,
                                dir_fd=descriptor,
                                follow_symlinks=False,
                            )
                            if not _same_file(info, rebound):
                                raise SafeReadError(
                                    f"directory {relative} changed during validation"
                                )
                        finally:
                            os.close(child)
                    elif entry.name.endswith(suffix):
                        if not stat.S_ISREG(info.st_mode):
                            raise SafeReadError(f"{relative} must be a regular file")
                        if len(documents) >= max_files:
                            raise SafeReadError(
                                f"checkout exceeds the {max_files}-document limit"
                            )
                        remaining = max_total_bytes - total_bytes
                        if remaining < 0:
                            raise SafeReadError(
                                "documentation exceeds the total byte limit"
                            )
                        byte_limit = min(max_file_bytes, remaining)
                        payload = self._read_at(
                            descriptor, entry.name, str(relative), byte_limit
                        )
                        total_bytes += len(payload)
                        if total_bytes > max_total_bytes:
                            raise SafeReadError(
                                "documentation exceeds the total byte limit"
                            )
                        try:
                            text = payload.decode("utf-8", errors="strict")
                        except UnicodeDecodeError as exc:
                            raise SafeReadError(
                                f"{relative} is not valid UTF-8"
                            ) from exc
                        documents.append((relative, text))

        walk(self._require_open(), ())
        self._assert_root_binding()
        documents.sort(key=lambda item: item[0].as_posix())
        return documents
