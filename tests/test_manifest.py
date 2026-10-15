import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_manifest_names():
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert plugin["name"] == "tech-writing"
    assert market["name"] == "tech-writing"
    assert market["plugins"][0]["name"] == "tech-writing"
    assert market["plugins"][0]["source"] == "./"
    assert plugin["version"] == market["plugins"][0]["version"]


def test_skill_frontmatter_name():
    text = (ROOT / "skills/tech-writer/SKILL.md").read_text()
    assert text.startswith("---\n")
    assert "\nname: tech-writer\n" in text
