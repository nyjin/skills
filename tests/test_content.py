import re
from pathlib import Path

import pytest

from conftest import ROOT, load_script

lint_doc = load_script("lint_doc")
SKILL = ROOT / "skills/tech-writer"
REFS = SKILL / "references"
SOURCES = ROOT / "docs/research/sources"
WINDOW = 25
PRINCIPLE = re.compile(r"^#{2,3} ([A-Z])-(\d+) ", re.M)
EXAMPLE = re.compile(r"^### ([A-Z])-(\d+)[a-z] ", re.M)
OWNERS = {
    "audience.md": "C", "doc-types.md": "DT", "structure.md": "ES",
    "style.md": "FGHIJP", "formatting.md": "LN", "visualization.md": "M",
    "review-checklist.md": "OQ",
}


def skill_docs():
    return sorted(p for p in SKILL.rglob("*.md"))


def norm(text):
    return re.sub(r"\s+", " ", text)


@pytest.fixture(scope="module")
def source_shingles():
    if not SOURCES.exists():
        pytest.skip("docs/research/sources 없음 (로컬 전용 자료)")
    shingles = set()
    for f in SOURCES.glob("*.md"):
        t = norm(f.read_text(encoding="utf-8"))
        shingles.update(t[i:i + WINDOW] for i in range(len(t) - WINDOW + 1))
    return shingles


def test_no_verbatim_copy_from_sources(source_shingles):
    hits = []
    for doc in skill_docs():
        t = norm(doc.read_text(encoding="utf-8"))
        for i in range(len(t) - WINDOW + 1):
            chunk = t[i:i + WINDOW]
            if chunk.strip() and not chunk.isascii() and chunk in source_shingles:
                hits.append(f"{doc.relative_to(ROOT)}: «{chunk}»")
                break
    assert not hits, "출처 원문과 25자 이상 일치:\n" + "\n".join(hits)


def test_each_reference_owns_its_principles():
    for name, letters in OWNERS.items():
        path = REFS / name
        assert path.exists(), f"{name} 없음"
        found = {m.group(1) for m in PRINCIPLE.finditer(path.read_text(encoding="utf-8"))}
        assert set(letters) <= found, f"{name}: {set(letters) - found} 원칙 없음"


def test_examples_cover_principles():
    ids = set()
    for name in OWNERS:
        path = REFS / name
        if path.exists():
            ids |= {f"{m.group(1)}-{m.group(2)}" for m in PRINCIPLE.finditer(path.read_text(encoding="utf-8"))}
    examples = (REFS / "examples.md").read_text(encoding="utf-8")
    pairs = [f"{m.group(1)}-{m.group(2)}" for m in EXAMPLE.finditer(examples)]
    per_letter = {}
    for pid in pairs:
        per_letter[pid[0]] = per_letter.get(pid[0], 0) + 1
    example_letters = set("EFGHIJLMNPS")
    for letter in example_letters:
        assert per_letter.get(letter, 0) >= 3, f"{letter}: 예시 {per_letter.get(letter, 0)}쌍 (최소 3)"
    for pid in ids:
        if pid[0] in example_letters:
            assert pid in pairs, f"{pid}: 예시 없음"


def test_examples_have_dont_do_why():
    text = (REFS / "examples.md").read_text(encoding="utf-8")
    blocks = re.split(r"^### ", text, flags=re.M)[1:]
    for b in blocks:
        head = b.splitlines()[0]
        assert "❌" in b and "✅" in b and "왜:" in b, f"{head}: ❌/✅/왜: 형식 누락"


@pytest.mark.parametrize("doc", skill_docs(), ids=lambda p: str(p.relative_to(SKILL)))
def test_skill_docs_pass_lint(doc):
    errors = [f for f in lint_doc.lint(doc.read_text(encoding="utf-8")) if f.level == "error"]
    assert not errors, errors
