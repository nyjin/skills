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
    blocks = re.split(r"^### (?=[A-Z]-\d+[a-z] )", text, flags=re.M)[1:]
    for b in blocks:
        head = b.splitlines()[0]
        assert "❌" in b and "✅" in b and "왜:" in b, f"{head}: ❌/✅/왜: 형식 누락"


@pytest.mark.parametrize("doc", skill_docs(), ids=lambda p: str(p.relative_to(SKILL)))
def test_skill_docs_pass_lint(doc):
    errors = [f for f in lint_doc.lint(doc.read_text(encoding="utf-8")) if f.level == "error"]
    assert not errors, errors


TEMPLATE_SECTIONS = {
    "tutorial.md": ["목표", "사전 준비", "결과 확인", "다음 단계"],
    "how-to.md": ["이 가이드로 할 수 있는 것", "사전 준비", "절차", "확인 방법", "문제가 생기면"],
    "troubleshooting.md": ["증상", "원인", "해결 방법", "해결 확인", "재발 방지"],
    "reference.md": ["개요", "사전 정보", "시그니처", "매개변수", "반환값", "오류", "예제"],
    "explanation.md": ["한 줄 정의", "등장 배경", "동작 원리", "대안과 비교", "활용"],
    "design-adr.md": ["요약", "배경", "목표와 비목표", "대안 비교", "결정", "영향과 리스크", "후속 작업"],
    "runbook.md": ["개요", "언제 쓰는가", "사전 확인", "절차", "롤백", "확인 방법", "연락처"],
    "postmortem.md": ["요약", "영향", "타임라인", "원인", "대응", "재발 방지 조치", "교훈"],
    "non-technical.md": ["왜", "무엇이 바뀌나", "영향", "할 일", "결정할 것"],
    "investigation.md": ["결정적인 곳", "값이 흘러가는 길", "데이터 확인", "주의할 점"],
}


@pytest.mark.parametrize("name,sections", TEMPLATE_SECTIONS.items())
def test_template_sections_in_order(name, sections):
    text = (REFS / "templates" / name).read_text(encoding="utf-8")
    h2 = [m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, re.M)]
    positions = [next(i for i, h in enumerate(h2) if h.startswith(s)) for s in sections]
    assert positions == sorted(positions), f"{name}: 섹션 순서 {h2}"


import subprocess

EXEMPLARS = REFS / "exemplars"
EXEMPLAR_NAMES = list(TEMPLATE_SECTIONS)


@pytest.mark.parametrize("name", EXEMPLAR_NAMES)
def test_exemplar_annotated_and_clean(name):
    text = (EXEMPLARS / name).read_text(encoding="utf-8")
    assert text.startswith("<!-- 독자:"), "맨 위 독자·어조 주석 없음"
    assert len(re.findall(r"<!-- ✓ [A-Z]-\d+:", text)) >= 8
    assert not [f for f in lint_doc.lint(text) if f.level == "error"]
    assert "TODO" not in text


@pytest.mark.parametrize("name,sections", TEMPLATE_SECTIONS.items())
def test_exemplar_follows_template(name, sections):
    text = (EXEMPLARS / name).read_text(encoding="utf-8")
    h2 = [m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, re.M)]
    for s in sections:
        assert any(h.startswith(s) for h in h2), f"{name}: '{s}' 섹션 없음"


@pytest.mark.slow
@pytest.mark.parametrize("name", EXEMPLAR_NAMES)
def test_exemplar_mermaid_renders(name, tmp_path):
    text = (EXEMPLARS / name).read_text(encoding="utf-8")
    blocks = re.findall(r"^```mermaid\n(.*?)^```", text, re.M | re.S)
    for i, block in enumerate(blocks):
        src = tmp_path / f"{i}.mmd"
        src.write_text(block)
        proc = subprocess.run(["bash", str(SKILL / "scripts/render-mermaid.sh"), str(src), str(tmp_path / f"{i}.svg")],
                              capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, f"{name} 블록 {i}: {proc.stderr}"


def test_evidence_level_principle_is_wired():
    style = (REFS / "style.md").read_text(encoding="utf-8")
    assert "## P-4 " in style
    for word in ("선언", "구현", "관측", "확인할 곳"):
        assert word in style.split("## P-4 ")[1].split("\n## ")[0]
    checklist = (REFS / "review-checklist.md").read_text(encoding="utf-8")
    assert "P-4" in checklist.split("## O-2 ")[1].split("\n## ")[0]
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "근거 수준" in next(l for l in skill.splitlines() if l.startswith("사실 검증:"))
    examples = (REFS / "examples.md").read_text(encoding="utf-8")
    assert "### P-4a " in examples


def test_doc_type_table_files_exist():
    text = (REFS / "doc-types.md").read_text(encoding="utf-8")
    paths = re.findall(r"`((?:templates|exemplars)/[\w-]+\.md)`", text)
    assert "templates/investigation.md" in paths
    missing = [p for p in paths if not (REFS / p).exists()]
    assert not missing, missing


def test_investigation_template_sections_in_order():
    t = (REFS / "templates/investigation.md").read_text(encoding="utf-8")
    heads = re.findall(r"^## (.+)$", t, re.M)
    assert heads == ["결정적인 곳", "값이 흘러가는 길", "데이터 확인", "주의할 점"]
