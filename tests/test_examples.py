from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_TOOL = ROOT / "scripts" / "contract_tool.py"


class ExampleTests(unittest.TestCase):
    def test_public_examples_validate_through_the_cli(self) -> None:
        examples = {
            "acceptance_evidence.json": "acceptance_evidence",
            "capability_preflight.json": "capability_preflight",
            "context_handoff.json": "context_handoff",
            "focused_check.json": "focused_check",
            "impact_scope.json": "impact_scope",
            "runtime_event_sequence.json": "runtime_event_sequence",
        }
        self.assertEqual(
            {path.name for path in (ROOT / "examples").glob("*.json")}, set(examples)
        )
        for filename, kind in examples.items():
            result = subprocess.run(
                [sys.executable, str(CONTRACT_TOOL), "validate", "--kind", kind, str(ROOT / "examples" / filename)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"], filename)

    def test_acceptance_example_exercises_structured_proofs(self) -> None:
        example = json.loads(
            (ROOT / "examples" / "acceptance_evidence.json").read_text(encoding="utf-8")
        )
        review_round = example["review_rounds"][0]
        self.assertEqual(review_round["review_result"]["status"], "CLEAN")
        self.assertEqual(
            review_round["review_coverage_proof"]["coverage_status"], "COMPLETE"
        )
        self.assertEqual(
            review_round["artifact_access_proof"]["workspace_compare_status"], "PASSED"
        )


if __name__ == "__main__":
    unittest.main()
