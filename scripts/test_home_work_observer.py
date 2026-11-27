#!/usr/bin/env python3
"""Regression guard for the Admin Home work-card observer recursion."""

from pathlib import Path


source = Path("admin/intake_workflow_ui.py").read_text(encoding="utf-8")
line = next((line for line in source.splitlines() if "const work=$('[data-home-work]')" in line), None)
assert line is not None, "Home work-card decorator exists"

assert "new MutationObserver(decorate).observe(work,{childList:true})" in line, (
    "the observer must watch direct list changes only; card text updates are descendants"
)
assert "subtree:true" not in line, "nested text updates must not recursively retrigger decorate()"
assert "if(detail&&detail.textContent!==message)detail.textContent=message" in line, (
    "the owner-warning text assignment must be idempotent"
)

print("PASS: Home work-card decoration cannot retrigger itself through descendant mutations")
