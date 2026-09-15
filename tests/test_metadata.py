from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))

import validate_metadata
from deadline_guard import fail_if_call_blocks


@unittest.skipIf(
    validate_metadata.yaml is None, "PyYAML is an optional development dependency"
)
class MetadataTests(unittest.TestCase):
    def copy_metadata(self, temporary: str) -> Path:
        checkout = Path(temporary) / "skill"
        (checkout / "agents").mkdir(parents=True)
        (checkout / "assets").mkdir()
        shutil.copy2(ROOT / "SKILL.md", checkout / "SKILL.md")
        shutil.copy2(
            ROOT / "agents" / "openai.yaml", checkout / "agents" / "openai.yaml"
        )
        shutil.copy2(
            ROOT / "assets" / "cdw-reviewer.toml",
            checkout / "assets" / "cdw-reviewer.toml",
        )
        return checkout

    def test_current_metadata_is_valid(self) -> None:
        report = validate_metadata.validate(ROOT)
        self.assertTrue(report["valid"], report)

    def test_malformed_skill_frontmatter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            skill = checkout / "SKILL.md"
            skill.write_text("---\nname: [broken\n---\nbody\n", encoding="utf-8")
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("not valid YAML" in error for error in report["errors"]))

    def test_frontmatter_delimiters_must_be_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            skill = checkout / "SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace("\n---\n", "\n---extra\n", 1),
                encoding="utf-8",
            )
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any("invalid frontmatter delimiters" in error for error in report["errors"])
        )

    def test_unsupported_skill_frontmatter_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            skill = checkout / "SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "---\n\n#", "unsupported: true\n---\n\n#", 1
                ),
                encoding="utf-8",
            )
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("unsupported key" in error for error in report["errors"]))

    def test_unfinished_body_todo_outside_code_fence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            skill = checkout / "SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8") + "\n[TODO: unfinished]\n",
                encoding="utf-8",
            )
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("unfinished TODO" in error for error in report["errors"]))

    def test_body_todo_inside_code_fence_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            skill = checkout / "SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8")
                + "\n```text\n[TODO: example only]\n```\n",
                encoding="utf-8",
            )
            report = validate_metadata.validate(checkout)
        self.assertTrue(report["valid"], report)

    def test_skill_name_and_description_constraints_fail(self) -> None:
        replacements = {
            "invalid name": (
                "name: collaborative-development-workflow",
                "name: Invalid_Name",
                "name must contain only lowercase",
            ),
            "nontext name": (
                "name: collaborative-development-workflow",
                "name: 123",
                "name must be text",
            ),
            "todo description": (
                'description: "Use on Linux',
                'description: "[TODO: Use on Linux',
                "unfinished TODO",
            ),
            "angle bracket description": (
                'description: "Use on Linux',
                'description: "Use <only> on Linux',
                "cannot contain angle brackets",
            ),
        }
        for label, (old, new, expected) in replacements.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                checkout = self.copy_metadata(temporary)
                skill = checkout / "SKILL.md"
                skill.write_text(
                    skill.read_text(encoding="utf-8").replace(old, new, 1),
                    encoding="utf-8",
                )
                report = validate_metadata.validate(checkout)
            self.assertFalse(report["valid"])
            self.assertTrue(
                any(expected in error for error in report["errors"]), report
            )

    def test_malformed_openai_metadata_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            metadata = checkout / "agents" / "openai.yaml"
            metadata.write_text("interface: [broken\n", encoding="utf-8")
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("not valid YAML" in error for error in report["errors"]))

    def test_duplicate_yaml_keys_fail_at_top_level_and_nested_levels(self) -> None:
        cases = {
            "top-level": (
                "interface:\n"
                "  display_name: first\n"
                "interface:\n"
                "  display_name: second\n"
                "policy:\n"
                "  allow_implicit_invocation: false\n"
            ),
            "nested": (
                "interface:\n"
                "  display_name: Collaborative Development Loop\n"
                "  short_description: Linux-only changes with frozen review\n"
                "  default_prompt: Use $collaborative-development-workflow now.\n"
                "policy:\n"
                "  allow_implicit_invocation: true\n"
                "  allow_implicit_invocation: false\n"
            ),
        }
        for label, content in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                checkout = self.copy_metadata(temporary)
                metadata = checkout / "agents" / "openai.yaml"
                metadata.write_text(content, encoding="utf-8")
                report = validate_metadata.validate(checkout)
            self.assertFalse(report["valid"])
            self.assertTrue(
                any("duplicate key" in error for error in report["errors"]), report
            )

        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            skill = checkout / "SKILL.md"
            skill.write_text(
                "---\n"
                "name: collaborative-development-workflow\n"
                "name: collaborative-development-workflow\n"
                "description: duplicate fixture\n"
                "---\nbody\n",
                encoding="utf-8",
            )
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any("duplicate key" in error for error in report["errors"]), report
        )

    def test_excessively_nested_yaml_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            metadata = checkout / "agents" / "openai.yaml"
            depth = validate_metadata.MAX_YAML_DEPTH + 2
            metadata.write_text(
                "interface: " + "[" * depth + "x" + "]" * depth + "\n",
                encoding="utf-8",
            )
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("nesting limit" in error for error in report["errors"]))

    def test_unknown_openai_metadata_fields_fail(self) -> None:
        replacements = {
            "top-level": ("policy:\n", "typo: true\npolicy:\n", "openai.yaml has unsupported"),
            "interface": (
                "  display_name:",
                "  display_nmae: typo\n  display_name:",
                "interface has unsupported",
            ),
            "policy": (
                "  allow_implicit_invocation:",
                "  allow_implicit_invocations: false\n  allow_implicit_invocation:",
                "policy has unsupported",
            ),
        }
        for label, (old, new, expected) in replacements.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                checkout = self.copy_metadata(temporary)
                metadata = checkout / "agents" / "openai.yaml"
                metadata.write_text(
                    metadata.read_text(encoding="utf-8").replace(old, new, 1),
                    encoding="utf-8",
                )
                report = validate_metadata.validate(checkout)
            self.assertFalse(report["valid"])
            self.assertTrue(any(expected in error for error in report["errors"]), report)

    def test_documented_optional_openai_metadata_fields_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            metadata = checkout / "agents" / "openai.yaml"
            metadata.write_text(
                metadata.read_text(encoding="utf-8").replace(
                    '  default_prompt:',
                    '  icon_small: "./assets/small.png"\n'
                    '  icon_large: "./assets/large.svg"\n'
                    '  brand_color: "#3B82F6"\n'
                    '  default_prompt:',
                    1,
                )
                + "\ndependencies:\n"
                + "  tools:\n"
                + "    - type: mcp\n"
                + "      value: github\n"
                + "      description: GitHub MCP server\n"
                + "      transport: streamable_http\n"
                + "      url: https://example.invalid/mcp\n",
                encoding="utf-8",
            )
            report = validate_metadata.validate(checkout)
        self.assertTrue(report["valid"], report)

    def test_malformed_reviewer_toml_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            reviewer = checkout / "assets" / "cdw-reviewer.toml"
            reviewer.write_text('name = "unterminated\n', encoding="utf-8")
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("not valid TOML" in error for error in report["errors"]))

    def test_writable_reviewer_sandbox_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            reviewer = checkout / "assets" / "cdw-reviewer.toml"
            reviewer.write_text(
                reviewer.read_text(encoding="utf-8").replace(
                    'sandbox_mode = "read-only"', 'sandbox_mode = "workspace-write"'
                ),
                encoding="utf-8",
            )
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "sandbox_mode must be read-only" in error
                for error in report["errors"]
            )
        )

    def test_reviewer_must_require_empty_attention_array(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            reviewer = checkout / "assets" / "cdw-reviewer.toml"
            reviewer.write_text(
                reviewer.read_text(encoding="utf-8").replace(
                    "empty JSON array", "empty list"
                ),
                encoding="utf-8",
            )
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "empty attention_required array" in error
                for error in report["errors"]
            )
        )

    def test_named_pipe_input_fails_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            skill = checkout / "SKILL.md"
            skill.unlink()
            os.mkfifo(skill)
            with fail_if_call_blocks():
                report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(any("cannot read SKILL.md" in error for error in report["errors"]))

    def test_metadata_symlink_to_endless_device_fails_without_reading_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            metadata = checkout / "agents" / "openai.yaml"
            metadata.unlink()
            os.symlink("/dev/zero", metadata)
            report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any("cannot read agents/openai.yaml" in error for error in report["errors"])
        )

    def test_oversized_skill_file_fails(self) -> None:
        limit = 8
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            skill = checkout / "SKILL.md"
            skill.write_bytes(b"x" * (limit + 1))
            with mock.patch.object(validate_metadata, "MAX_SKILL_BYTES", limit):
                report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "cannot read SKILL.md" in error
                and f"{limit}-byte limit" in error
                for error in report["errors"]
            ),
            report,
        )

    def test_oversized_openai_metadata_fails(self) -> None:
        limit = 8
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            metadata = checkout / "agents" / "openai.yaml"
            metadata.write_bytes(b"x" * (limit + 1))
            with mock.patch.object(validate_metadata, "MAX_METADATA_BYTES", limit):
                report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "cannot read agents/openai.yaml" in error
                and f"{limit}-byte limit" in error
                for error in report["errors"]
            ),
            report,
        )

    def test_oversized_reviewer_configuration_fails(self) -> None:
        limit = 8
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self.copy_metadata(temporary)
            reviewer = checkout / "assets" / "cdw-reviewer.toml"
            reviewer.write_bytes(b"x" * (limit + 1))
            with mock.patch.object(validate_metadata, "MAX_REVIEWER_BYTES", limit):
                report = validate_metadata.validate(checkout)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "cannot read assets/cdw-reviewer.toml" in error
                and f"{limit}-byte limit" in error
                for error in report["errors"]
            ),
            report,
        )


if __name__ == "__main__":
    unittest.main()
