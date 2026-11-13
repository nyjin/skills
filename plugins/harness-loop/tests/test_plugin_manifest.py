import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_marketplace_lists_harness_loop_with_matching_version():
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    entry = next(p for p in market["plugins"] if p["name"] == "harness-loop")
    assert entry["source"] == "./plugins/harness-loop"
    plugin = json.loads(
        (ROOT / "plugins/harness-loop/.claude-plugin/plugin.json").read_text()
    )
    assert plugin["name"] == "harness-loop"
    assert entry["version"] == plugin["version"]
