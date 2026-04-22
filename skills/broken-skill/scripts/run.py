#!/usr/bin/env python3
"""Intentionally broken — always crashes."""

import sys

print("Starting broken skill...", flush=True)
raise RuntimeError("This skill is intentionally broken for testing error handling.")
