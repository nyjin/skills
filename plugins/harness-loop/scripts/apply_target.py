#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""반영 대상 파일의 소유 환경(repo|local|plugin_cache|new) 판정."""
import json
import os
import subprocess
import sys

RULE_FILES = ("CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md")


def _toplevel(directory: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return os.path.realpath(out) if r.returncode == 0 and out else None


def _rules(root: str | None) -> list[str]:
    if not root:
        return []
    return [p for p in (os.path.join(root, f) for f in RULE_FILES) if os.path.exists(p)]


def _in_plugin_cache(real: str) -> bool:
    for sub in ("cache", "marketplaces"):
        base = os.path.realpath(os.path.expanduser(f"~/.claude/plugins/{sub}"))
        if real == base or real.startswith(base + os.sep):
            return True
    return False


def classify(path: str) -> dict:
    real = os.path.realpath(os.path.expanduser(path))
    if _in_plugin_cache(real):
        return {"kind": "plugin_cache", "real_path": real, "root": None, "rules": []}
    if os.path.exists(real):
        d = real if os.path.isdir(real) else os.path.dirname(real)
        root = _toplevel(d)
        return {"kind": "repo" if root else "local", "real_path": real, "root": root, "rules": _rules(root)}
    parent = os.path.dirname(real)
    while parent and not os.path.isdir(parent) and parent != os.path.dirname(parent):
        parent = os.path.dirname(parent)
    root = _toplevel(parent)
    return {"kind": "new", "real_path": real, "root": root, "rules": _rules(root)}


if __name__ == "__main__":
    print(json.dumps(classify(sys.argv[1]), ensure_ascii=False))
