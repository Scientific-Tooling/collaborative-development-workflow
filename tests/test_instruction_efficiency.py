from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_SKILL_BYTES = 6_500
MAX_DEFAULT_CORE_BYTES = 12_000


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


if __name__ == "__main__":
    unittest.main()
