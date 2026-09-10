#!/usr/bin/env python3
"""Diagnose whether the local workflow helpers are usable."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_previous_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    from contract_tool import ContractError, digest_record, load_json_file, validate_record
    from snapshot_tool import git_executable_probe, platform_capabilities
finally:
    sys.dont_write_bytecode = _previous_dont_write_bytecode
    del _previous_dont_write_bytecode


SUPPORTED_PYTHON = {(3, minor) for minor in range(10, 14)}
REQUIRED_PATHS = (
    "SKILL.md",
    "README.md",
    "CHANGELOG.md",
    "references/contracts-v2.json",
    "scripts/contract_tool.py",
    "scripts/snapshot_tool.py",
    "examples/capability_preflight.json",
    "examples/acceptance_evidence.json",
)
MARKDOWN_LINK_RE = re.compile(r"\[[^]]+\]\(([^)]+)\)")
RELEASE_RE = re.compile(r"^## \[([^]]+)\]", re.MULTILINE)


def _result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "PASSED" if passed else "FAILED", "detail": detail}


def _documentation_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for document in sorted(root.rglob("*.md")):
        if ".git" in document.parts:
            continue
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_RE.findall(text):
            target = raw_target.strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (document.parent / target).resolve().exists():
                errors.append(f"{document.relative_to(root)} -> {target}")
    return errors


def diagnose(root_arg: str, preflight_path: str | None = None) -> tuple[int, dict[str, Any]]:
    root = Path(root_arg).resolve()
    checks: list[dict[str, Any]] = []
    python_version = sys.version_info[:2]
    checks.append(
        _result(
            "python_version",
            python_version in SUPPORTED_PYTHON,
            f"{python_version[0]}.{python_version[1]} (supported: 3.10-3.13)",
        )
    )
    missing = [path for path in REQUIRED_PATHS if not (root / path).is_file()]
    checks.append(
        _result(
            "required_files",
            not missing,
            "all required files present" if not missing else f"missing: {', '.join(missing)}",
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
    skill_path = root / "SKILL.md"
    frontmatter_ready = False
    if skill_path.is_file():
        skill_text = skill_path.read_text(encoding="utf-8")
        frontmatter_ready = bool(
            skill_text.startswith("---\n")
            and "\nname: collaborative-development-workflow\n" in skill_text
            and "\ndescription:" in skill_text.split("---", 2)[1]
        )
    checks.append(
        _result(
            "skill_frontmatter",
            frontmatter_ready,
            "name and description present" if frontmatter_ready else "invalid SKILL.md frontmatter",
        )
    )
    try:
        link_errors = _documentation_errors(root)
    except (OSError, UnicodeError) as exc:
        link_errors = [str(exc)]
    checks.append(
        _result(
            "documentation_links",
            not link_errors,
            "relative links resolve" if not link_errors else "; ".join(link_errors[:8]),
        )
    )

    release = "UNKNOWN"
    changelog = root / "CHANGELOG.md"
    if changelog.is_file():
        match = RELEASE_RE.search(changelog.read_text(encoding="utf-8"))
        if match:
            release = match.group(1)

    runtime: dict[str, Any] = {
        "attested": False,
        "result": "UNATTESTED",
        "detail": "supply --preflight to validate an observed or authoritative record",
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
                    "attested": True,
                    "authority": preflight["authority"],
                    "mode": preflight["mode"],
                    "result": preflight["result"],
                    "digest": digest_record(preflight, "capability_preflight"),
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
