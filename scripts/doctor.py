#!/usr/bin/env python3
"""Diagnose whether the local workflow helpers are usable."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

_previous_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    from contract_tool import ContractError, digest_record, load_json_file, validate_record
    from safe_read import SafeReadError, SafeRoot
    from snapshot_tool import git_executable_probe, platform_capabilities
finally:
    sys.dont_write_bytecode = _previous_dont_write_bytecode
    del _previous_dont_write_bytecode


SUPPORTED_PYTHON = {(3, minor) for minor in range(10, 14)}
REQUIRED_PATHS = (
    "SKILL.md",
    "README.md",
    "CHANGELOG.md",
    "agents/openai.yaml",
    "assets/cdw-reviewer.toml",
    "evals/doctor-pretty.json",
    "references/contracts-v2.json",
    "scripts/behavior_eval.py",
    "scripts/contract_tool.py",
    "scripts/safe_read.py",
    "scripts/snapshot_tool.py",
    "scripts/workflow_tool.py",
    "scripts/validate_metadata.py",
    "examples/capability_preflight.json",
    "examples/acceptance_evidence.json",
)
MARKDOWN_LINK_RE = re.compile(r"\[[^]]+\]\(([^)]+)\)")
RELEASE_RE = re.compile(r"^## \[([^]]+)\]", re.MULTILINE)
MAX_CORE_FILE_BYTES = 1024 * 1024
MAX_DOCUMENT_DEPTH = 64
MAX_CHECKOUT_ENTRIES = 10_000
MAX_DOCUMENTS = 512
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_ALL_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_LINKS = 10_000
MAX_DOCUMENT_LINK_ERRORS = 256
MAX_LINK_ERROR_CHARACTERS = 1024


def _result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "PASSED" if passed else "FAILED", "detail": detail}


def _bounded_link_error(document: PurePosixPath, target: str) -> str:
    value = f"{document} -> {target}"
    if len(value) <= MAX_LINK_ERROR_CHARACTERS:
        return value
    return value[: MAX_LINK_ERROR_CHARACTERS - 3] + "..."


def _documentation_errors(reader: SafeRoot) -> list[str]:
    errors: list[str] = []
    links = 0
    documents = reader.scan_text_files(
        suffix=".md",
        skip_top_level={".git"},
        max_depth=MAX_DOCUMENT_DEPTH,
        max_entries=MAX_CHECKOUT_ENTRIES,
        max_files=MAX_DOCUMENTS,
        max_file_bytes=MAX_DOCUMENT_BYTES,
        max_total_bytes=MAX_ALL_DOCUMENT_BYTES,
    )
    for document, text in documents:
        for match in MARKDOWN_LINK_RE.finditer(text):
            links += 1
            if links > MAX_DOCUMENT_LINKS:
                raise SafeReadError(
                    f"documentation exceeds the {MAX_DOCUMENT_LINKS}-link limit"
                )
            raw_target = match.group(1)
            target = raw_target.strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            candidate = posixpath.normpath(
                posixpath.join(document.parent.as_posix(), target)
            )
            if (
                candidate in {"", ".", ".."}
                or candidate.startswith("../")
                or candidate.startswith("/")
                or not reader.path_exists(PurePosixPath(candidate))
            ):
                errors.append(_bounded_link_error(document, target))
                if len(errors) >= MAX_DOCUMENT_LINK_ERRORS:
                    raise SafeReadError(
                        "documentation has too many unresolved relative links"
                    )
    reader.assert_bound()
    return errors


def diagnose(root_arg: str, preflight_path: str | None = None) -> tuple[int, dict[str, Any]]:
    root = Path(os.path.abspath(os.fspath(root_arg)))
    checks: list[dict[str, Any]] = []
    python_version = sys.version_info[:2]
    checks.append(
        _result(
            "python_version",
            python_version in SUPPORTED_PYTHON,
            f"{python_version[0]}.{python_version[1]} (supported: 3.10-3.13)",
        )
    )
    snapshot_capabilities = platform_capabilities()
    snapshot_ready = all(snapshot_capabilities.values())
    capability_detail = ", ".join(
        f"{name}={'yes' if available else 'no'}"
        for name, available in sorted(snapshot_capabilities.items())
    )
    checks.append(
        _result(
            "snapshot_platform",
            snapshot_ready,
            capability_detail,
        )
    )
    git_ready, git_detail = git_executable_probe()
    checks.append(_result("git_executable", git_ready, git_detail))
    release = "UNKNOWN"
    try:
        reader = SafeRoot(root)
    except (OSError, SafeReadError) as exc:
        detail = f"cannot safely open checkout: {exc}"
        checks.extend(
            (
                _result("required_files", False, detail),
                _result("skill_frontmatter", False, detail),
                _result("documentation_links", False, detail),
            )
        )
    else:
        with reader:
            missing = [
                path for path in REQUIRED_PATHS if not reader.is_regular_file(path)
            ]
            checks.append(
                _result(
                    "required_files",
                    not missing,
                    (
                        "all required files present"
                        if not missing
                        else f"missing or unsafe: {', '.join(missing)}"
                    ),
                )
            )

            frontmatter_ready = False
            try:
                skill_text = reader.read_text("SKILL.md", MAX_CORE_FILE_BYTES)
            except (OSError, SafeReadError):
                pass
            else:
                frontmatter_ready = bool(
                    skill_text.startswith("---\n")
                    and "\nname: collaborative-development-workflow\n" in skill_text
                    and "\ndescription:" in skill_text.split("---", 2)[1]
                )
            checks.append(
                _result(
                    "skill_frontmatter",
                    frontmatter_ready,
                    (
                        "name and description present"
                        if frontmatter_ready
                        else "invalid or unsafe SKILL.md frontmatter"
                    ),
                )
            )

            try:
                link_errors = _documentation_errors(reader)
            except (OSError, SafeReadError) as exc:
                link_errors = [str(exc)]
            checks.append(
                _result(
                    "documentation_links",
                    not link_errors,
                    (
                        "relative links resolve"
                        if not link_errors
                        else "; ".join(link_errors[:8])
                    ),
                )
            )

            try:
                changelog_text = reader.read_text(
                    "CHANGELOG.md", MAX_CORE_FILE_BYTES
                )
            except (OSError, SafeReadError):
                pass
            else:
                match = RELEASE_RE.search(changelog_text)
                if match:
                    release = match.group(1)

    runtime: dict[str, Any] = {
        "attested": False,
        "result": "UNATTESTED",
        "detail": "doctor has not observed the live runtime",
    }
    if preflight_path is not None:
        try:
            preflight = load_json_file(preflight_path)
            errors = validate_record(preflight, "capability_preflight")
            if errors:
                checks.append(_result("capability_preflight", False, "; ".join(errors)))
            else:
                checks.append(_result("capability_preflight", True, "supplied record is valid"))
                runtime = {
                    "attested": False,
                    "authority": preflight["authority"],
                    "mode": preflight["mode"],
                    "result": "UNATTESTED",
                    "record_result": preflight["result"],
                    "run_id": preflight["run_id"],
                    "read_only_enforcement": preflight["read_only_enforcement"],
                    "artifact_only_read_enforcement": preflight[
                        "artifact_only_read_enforcement"
                    ],
                    "digest": digest_record(preflight, "capability_preflight"),
                    "detail": (
                        "record shape is valid; doctor did not observe its runtime "
                        "facts or freshness"
                    ),
                }
        except (ContractError, OSError, UnicodeError, ValueError, TypeError) as exc:
            checks.append(_result("capability_preflight", False, str(exc)))

    valid = all(check["status"] == "PASSED" for check in checks)
    return (0 if valid else 2), {
        "valid": valid,
        "release": release,
        "root": str(root),
        "checks": checks,
        "runtime_readiness": runtime,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="workflow skill checkout")
    parser.add_argument("--preflight", help="optional capability-preflight-v2 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code, report = diagnose(args.root, args.preflight)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
