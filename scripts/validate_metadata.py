#!/usr/bin/env python3
"""Validate the Skill metadata and bundled reviewer configuration."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

_previous_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    from safe_read import SafeReadError, SafeRoot
finally:
    sys.dont_write_bytecode = _previous_dont_write_bytecode
    del _previous_dont_write_bytecode

try:
    import yaml
except ImportError:  # pragma: no cover - reported by main with a useful message
    yaml = None


if yaml is not None:
    class _UniqueKeySafeLoader(yaml.SafeLoader):
        """Load safe YAML while rejecting ambiguous duplicate mapping keys."""

        def __init__(self, stream):
            super().__init__(stream)
            self._cdw_node_count = 0
            self._cdw_node_depth = 0

        def compose_node(self, parent, index):
            self._cdw_node_count += 1
            self._cdw_node_depth += 1
            try:
                if self._cdw_node_count > MAX_YAML_NODES:
                    raise yaml.composer.ComposerError(
                        None,
                        None,
                        "YAML exceeds the node limit",
                        self.peek_event().start_mark,
                    )
                if self._cdw_node_depth > MAX_YAML_DEPTH:
                    raise yaml.composer.ComposerError(
                        None,
                        None,
                        "YAML exceeds the nesting limit",
                        self.peek_event().start_mark,
                    )
                return super().compose_node(parent, index)
            finally:
                self._cdw_node_depth -= 1

        def construct_mapping(self, node, deep=False):
            if not isinstance(node, yaml.MappingNode):
                return super().construct_mapping(node, deep=deep)
            self.flatten_mapping(node)
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                try:
                    duplicate = key in mapping
                except TypeError as exc:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        "found an unhashable mapping key",
                        key_node.start_mark,
                    ) from exc
                if duplicate:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        "found duplicate key",
                        key_node.start_mark,
                    )
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping
else:  # pragma: no cover - loader use is guarded by validate()
    _UniqueKeySafeLoader = None

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - exercised on Python 3.10 in CI
    try:
        import tomli as tomllib
    except ImportError:  # pragma: no cover - reported by validate with a useful message
        tomllib = None


SKILL_NAME = "collaborative-development-workflow"
MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_YAML_DEPTH = 64
MAX_YAML_NODES = 10_000
MAX_VALIDATION_ERRORS = 256
ALLOWED_FRONTMATTER_KEYS = {
    "allowed-tools",
    "description",
    "license",
    "metadata",
    "name",
}
REVIEWER_FIELDS = {
    "approval_policy",
    "description",
    "developer_instructions",
    "name",
    "sandbox_mode",
}
OPENAI_METADATA_FIELDS = {"dependencies", "interface", "policy"}
INTERFACE_FIELDS = {
    "brand_color",
    "default_prompt",
    "display_name",
    "icon_large",
    "icon_small",
    "short_description",
}
POLICY_FIELDS = {"allow_implicit_invocation"}
DEPENDENCIES_FIELDS = {"tools"}
DEPENDENCY_TOOL_FIELDS = {
    "description",
    "transport",
    "type",
    "url",
    "value",
}
MAX_SKILL_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 256 * 1024
MAX_REVIEWER_BYTES = 256 * 1024


class _BoundedErrorList(list):
    def append(self, item):
        if len(self) < MAX_VALIDATION_ERRORS:
            super().append(item)
        elif len(self) == MAX_VALIDATION_ERRORS:
            super().append("additional validation errors omitted")


def _display_key(value):
    text = str(value)
    return text if len(text) <= 120 else text[:117] + "..."


def _load_yaml(text, label, errors):
    try:
        value = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        errors.append("{} is not valid YAML: {}".format(label, exc))
        return None
    if not isinstance(value, dict):
        errors.append("{} must contain a YAML object".format(label))
        return None
    return value


def _validate_skill(skill_text, errors):
    if not skill_text.startswith("---"):
        errors.append("SKILL.md has no YAML frontmatter")
        return

    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", skill_text, re.DOTALL)
    if match is None:
        errors.append("SKILL.md has invalid frontmatter delimiters")
        return

    frontmatter = _load_yaml(match.group(1), "SKILL.md frontmatter", errors)
    if frontmatter is None:
        return

    unexpected = set(frontmatter) - ALLOWED_FRONTMATTER_KEYS
    if unexpected:
        errors.append(
            "SKILL.md frontmatter has unsupported key(s): {}".format(
                ", ".join(sorted(_display_key(key) for key in unexpected))
            )
        )

    if "name" not in frontmatter:
        errors.append("SKILL.md frontmatter is missing name")
    else:
        name = frontmatter["name"]
        if not isinstance(name, str):
            errors.append("SKILL.md name must be text")
        else:
            name = name.strip()
            if name and not re.fullmatch(r"[a-z0-9-]+", name):
                errors.append(
                    "SKILL.md name must contain only lowercase letters, digits, "
                    "and hyphens"
                )
            if name.startswith("-") or name.endswith("-") or "--" in name:
                errors.append(
                    "SKILL.md name cannot start or end with a hyphen or contain "
                    "consecutive hyphens"
                )
            if len(name) > MAX_SKILL_NAME_LENGTH:
                errors.append(
                    "SKILL.md name must be at most {} characters".format(
                        MAX_SKILL_NAME_LENGTH
                    )
                )
            if name != SKILL_NAME:
                errors.append("SKILL.md name must be {}".format(SKILL_NAME))

    if "description" not in frontmatter:
        errors.append("SKILL.md frontmatter is missing description")
    else:
        description = frontmatter["description"]
        if not isinstance(description, str):
            errors.append("SKILL.md description must be text")
        else:
            description = description.strip()
            if not description:
                errors.append("SKILL.md description must be nonempty text")
            if description.startswith("[TODO:"):
                errors.append(
                    "SKILL.md description contains an unfinished TODO placeholder"
                )
            if "<" in description or ">" in description:
                errors.append("SKILL.md description cannot contain angle brackets")
            if len(description) > MAX_DESCRIPTION_LENGTH:
                errors.append(
                    "SKILL.md description must be at most {} characters".format(
                        MAX_DESCRIPTION_LENGTH
                    )
                )

    fence_marker = None
    fence_length = 0
    for line in skill_text[match.end() :].splitlines():
        fence = re.match(
            r"^[ \t]*(?:(?:[-+*]|\d+[.)])[ \t]+)?(`{3,}|~{3,})(.*)$", line
        )
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker[0]
                fence_length = len(marker)
            elif (
                marker[0] == fence_marker
                and len(marker) >= fence_length
                and not fence.group(2).strip()
            ):
                fence_marker = None
                fence_length = 0
            continue

        if fence_marker is None and re.fullmatch(
            r"[ ]{0,3}\[TODO:[^\n]*\][ \t]*", line
        ):
            errors.append(
                "SKILL.md instructions contain an unfinished TODO placeholder"
            )
            break


def _validate_reviewer(reader, errors):
    if tomllib is None:
        errors.append(
            "TOML support is required; use Python 3.11+ or install requirements-dev.txt"
        )
        return

    try:
        reviewer_text = reader.read_text(
            "assets/cdw-reviewer.toml", MAX_REVIEWER_BYTES
        )
    except (OSError, SafeReadError) as exc:
        errors.append("cannot read assets/cdw-reviewer.toml: {}".format(exc))
        return
    try:
        reviewer = tomllib.loads(reviewer_text)
    except (tomllib.TOMLDecodeError, UnicodeError, RecursionError) as exc:
        errors.append("assets/cdw-reviewer.toml is not valid TOML: {}".format(exc))
        return
    if not isinstance(reviewer, dict):  # Defensive: TOML parsers normally return dict.
        errors.append("assets/cdw-reviewer.toml must contain a TOML table")
        return

    keys = set(reviewer)
    missing = REVIEWER_FIELDS - keys
    unexpected = keys - REVIEWER_FIELDS
    if missing:
        errors.append(
            "assets/cdw-reviewer.toml is missing field(s): {}".format(
                ", ".join(sorted(missing))
            )
        )
    if unexpected:
        errors.append(
            "assets/cdw-reviewer.toml has unsupported field(s): {}".format(
                ", ".join(sorted(_display_key(key) for key in unexpected))
            )
        )

    for field in REVIEWER_FIELDS:
        value = reviewer.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(
                "assets/cdw-reviewer.toml {} must be nonempty text".format(field)
            )

    if reviewer.get("name") != "cdw_reviewer":
        errors.append("assets/cdw-reviewer.toml name must be cdw_reviewer")
    if reviewer.get("sandbox_mode") != "read-only":
        errors.append("assets/cdw-reviewer.toml sandbox_mode must be read-only")
    if reviewer.get("approval_policy") != "never":
        errors.append("assets/cdw-reviewer.toml approval_policy must be never")

    instructions = reviewer.get("developer_instructions")
    if isinstance(instructions, str):
        lowered = instructions.lower()
        if "role-result-v2" not in lowered:
            errors.append(
                "assets/cdw-reviewer.toml developer_instructions must mention "
                "role-result-v2"
            )
        if "attention_required" not in instructions or not re.search(
            r"empty(?:\s+json)?\s+array", lowered
        ):
            errors.append(
                "assets/cdw-reviewer.toml developer_instructions must require an "
                "empty attention_required array"
            )


def _unsupported_fields(value, allowed, label, errors):
    unexpected = set(value) - allowed
    if unexpected:
        errors.append(
            "{} has unsupported field(s): {}".format(
                label, ", ".join(sorted(_display_key(key) for key in unexpected))
            )
        )


def _validate_dependencies(dependencies, errors):
    if not isinstance(dependencies, dict):
        errors.append("agents/openai.yaml dependencies must be an object")
        return
    _unsupported_fields(
        dependencies, DEPENDENCIES_FIELDS, "agents/openai.yaml dependencies", errors
    )
    tools = dependencies.get("tools")
    if not isinstance(tools, list):
        errors.append("dependencies.tools must be a list")
        return
    for index, tool in enumerate(tools):
        label = "dependencies.tools[{}]".format(index)
        if not isinstance(tool, dict):
            errors.append("{} must be an object".format(label))
            continue
        _unsupported_fields(tool, DEPENDENCY_TOOL_FIELDS, label, errors)
        missing = DEPENDENCY_TOOL_FIELDS - set(tool)
        if missing:
            errors.append(
                "{} is missing field(s): {}".format(
                    label, ", ".join(sorted(missing))
                )
            )
        for field in DEPENDENCY_TOOL_FIELDS:
            value = tool.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append("{}.{} must be nonempty text".format(label, field))
        if tool.get("type") != "mcp":
            errors.append("{}.type must be mcp".format(label))


def validate(root_arg):
    root = Path(os.path.abspath(os.fspath(root_arg)))
    errors = _BoundedErrorList()
    if yaml is None:
        return {
            "valid": False,
            "root": str(root),
            "errors": ["PyYAML is required; install requirements-dev.txt"],
        }

    try:
        reader = SafeRoot(root)
    except (OSError, SafeReadError) as exc:
        return {
            "valid": False,
            "root": str(root),
            "errors": ["cannot safely open Skill checkout: {}".format(exc)],
        }
    with reader:
        try:
            skill_text = reader.read_text("SKILL.md", MAX_SKILL_BYTES)
        except (OSError, SafeReadError) as exc:
            errors.append("cannot read SKILL.md: {}".format(exc))
            skill_text = None

        if skill_text is not None:
            _validate_skill(skill_text, errors)

        try:
            metadata_text = reader.read_text(
                "agents/openai.yaml", MAX_METADATA_BYTES
            )
        except (OSError, SafeReadError) as exc:
            errors.append("cannot read agents/openai.yaml: {}".format(exc))
            metadata_text = None

        _validate_reviewer(reader, errors)
    metadata = (
        _load_yaml(metadata_text, "agents/openai.yaml", errors)
        if metadata_text is not None
        else None
    )
    if metadata is not None:
        _unsupported_fields(
            metadata, OPENAI_METADATA_FIELDS, "agents/openai.yaml", errors
        )
        interface = metadata.get("interface")
        if not isinstance(interface, dict):
            errors.append("agents/openai.yaml interface must be an object")
        else:
            _unsupported_fields(interface, INTERFACE_FIELDS, "interface", errors)
            for field in ("display_name", "short_description", "default_prompt"):
                value = interface.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append("interface.{} must be nonempty text".format(field))
            for field in ("icon_small", "icon_large"):
                value = interface.get(field)
                if value is not None and (
                    not isinstance(value, str) or not value.strip()
                ):
                    errors.append("interface.{} must be nonempty text".format(field))
            brand_color = interface.get("brand_color")
            if brand_color is not None and (
                not isinstance(brand_color, str)
                or re.fullmatch(r"#[0-9A-Fa-f]{6}", brand_color) is None
            ):
                errors.append("interface.brand_color must be a six-digit hex color")
            short_description = interface.get("short_description")
            if (
                isinstance(short_description, str)
                and not 25 <= len(short_description) <= 64
            ):
                errors.append("interface.short_description must be 25-64 characters")
            default_prompt = interface.get("default_prompt")
            if (
                isinstance(default_prompt, str)
                and "$" + SKILL_NAME not in default_prompt
            ):
                errors.append("interface.default_prompt must mention $" + SKILL_NAME)

        policy = metadata.get("policy")
        if not isinstance(policy, dict):
            errors.append("agents/openai.yaml policy must be an object")
        else:
            _unsupported_fields(policy, POLICY_FIELDS, "policy", errors)
            if policy.get("allow_implicit_invocation") is not False:
                errors.append("policy.allow_implicit_invocation must be false")

        if "dependencies" in metadata:
            _validate_dependencies(metadata["dependencies"], errors)

    return {"valid": not errors, "root": str(root), "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Skill checkout to validate")
    args = parser.parse_args(argv)
    report = validate(args.root)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
