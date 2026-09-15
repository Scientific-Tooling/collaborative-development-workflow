from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path


def fixture_git_environment(
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a caller-independent environment for fixture Git commands."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    if overrides:
        environment.update(overrides)
    return environment


class GitRepositoryTemplate:
    """Build a Git fixture once, then give each test an isolated copy."""

    def __init__(self, build: Callable[[Path], None]) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="cdw-git-fixture-")
        self.path = Path(self._temporary.name) / "repository"
        try:
            build(self.path)
        except BaseException:
            self._temporary.cleanup()
            raise

    def copy_to(self, destination: Path) -> Path:
        shutil.copytree(self.path, destination, symlinks=True)
        return destination

    def cleanup(self) -> None:
        self._temporary.cleanup()
