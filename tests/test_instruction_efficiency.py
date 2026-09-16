from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_SKILL_BYTES = 6_500
MAX_DEFAULT_CORE_BYTES = 12_000
MAX_CONDITIONAL_REFERENCE_BYTES = 2_400
REFERENCE_TOPICS = {
    "agent-templates.md": "roles",
    "context-rollover.md": "rollover",
    "coordination-protocol.md": "coordination",
    "failure-and-reporting.md": "report",
    "migrating-v1-to-v2.md": "migration",
    "model-selection.md": "model",
    "review-recovery-strict.md": "strict",
    "review-recovery.md": "recovery",
    "review-runtime-strict.md": "strict",
    "review-runtime.md": "review",
    "task-contracts.md": "workflow",
    "workflow.md": "workflow",
}


class InstructionEfficiencyTests(unittest.TestCase):
    def test_skill_entrypoint_stays_within_its_byte_budget(self) -> None:
        size = len((ROOT / "SKILL.md").read_bytes())
        self.assertLessEqual(
            size,
            MAX_SKILL_BYTES,
            f"SKILL.md is {size} bytes; limit is {MAX_SKILL_BYTES}",
        )

    def test_default_core_instructions_stay_within_their_byte_budget(self) -> None:
        paths = (ROOT / "SKILL.md", ROOT / "references" / "workflow.md")
        sizes = {path.name: len(path.read_bytes()) for path in paths}
        total = sum(sizes.values())
        self.assertLessEqual(
            total,
            MAX_DEFAULT_CORE_BYTES,
            f"default core instructions are {total} bytes: {sizes}; "
            f"limit is {MAX_DEFAULT_CORE_BYTES}",
        )

    def test_conditional_references_are_thin_routes_to_python_guidance(self) -> None:
        references = ROOT / "references"
        for filename, topic in REFERENCE_TOPICS.items():
            with self.subTest(filename=filename):
                path = references / filename
                content = path.read_text(encoding="utf-8")
                self.assertLessEqual(
                    len(content.encode("utf-8")),
                    MAX_CONDITIONAL_REFERENCE_BYTES,
                    f"{filename} should route details to workflow_tool.py",
                )
                self.assertIn("workflow_tool.py", content)
                self.assertIn(f"guide --topic {topic}", content)


if __name__ == "__main__":
    unittest.main()
