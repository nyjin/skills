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


import re


def test_skill_md_size_and_references_exist():
    skill = ROOT / "skills/tech-writer"
    text = (skill / "SKILL.md").read_text()
    assert len(text.splitlines()) <= 500
    for ref in set(re.findall(r"`((?:references|scripts|config)/[^`]+?)`", text)):
        ref = ref.split()[0]
        if "<" in ref:
            continue
        assert (skill / ref).exists(), f"SKILL.md가 가리키는 {ref} 없음"


def test_description_mentions_key_triggers():
    text = (ROOT / "skills/tech-writer/SKILL.md").read_text()
    desc = text.split("description:", 1)[1].split("\n---", 1)[0]
    for word in ["README", "트러블슈팅", "설계 문서", "포스트모템", "런북", "korean-proofreader"]:
        assert word in desc
