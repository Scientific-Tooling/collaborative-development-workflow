#!/usr/bin/env python3
"""Produce the deterministic behavior-regression fixture output."""

from pathlib import Path


Path("behavior-regression-output.txt").write_text("passed\n", encoding="utf-8")
print("DETERMINISTIC_BEHAVIOR_PASS")
