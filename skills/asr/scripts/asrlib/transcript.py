"""전사본 문서 모델, 마크다운 렌더링, 출력 경로 선택."""
import json
import os
import re
from pathlib import Path

_SENTENCE_GAP = re.compile(r"(?<=[.?!。])\s+")
_FAILED_TEXT = "이 구간은 인식에 실패했습니다."


def resolve(speakers: dict, sid: str) -> str:
    seen = set()
    while "merged_into" in speakers[sid] and sid not in seen:
        seen.add(sid)
        sid = speakers[sid]["merged_into"]
    return sid


def display_name(speakers: dict, sid: str) -> str:
    sp = speakers[resolve(speakers, sid)]
    return sp.get("name") or f"화자 {sp['label_no']}"


def _ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def render_markdown(doc: dict) -> str:
    items = [(s["start"], 0, s) for s in doc["segments"]]
    items += [(f["start"], 1, f) for f in doc.get("failed_ranges", [])]
    items.sort(key=lambda it: (it[0], it[1]))

    paragraphs = []  # [name | None, start, text]
    for start, kind, item in items:
        if kind == 1:
            text = f"> ⚠ [{_ts(item['start'])}–{_ts(item['end'])}] {_FAILED_TEXT}"
            paragraphs.append([None, start, text])
            continue
        name = display_name(doc["speakers"], item["speaker"])
        if paragraphs and paragraphs[-1][0] == name:
            paragraphs[-1][2] += " " + item["text"]
        else:
            paragraphs.append([name, start, item["text"]])

    lines = [
        text if name is None else f"**[{_ts(start)}] {name}:** {text}"
        for name, start, text in paragraphs
    ]
    return "\n\n".join(lines) + "\n" if lines else ""


def first_lines(doc: dict, n: int = 2) -> dict:
    collected = {}  # 표시 이름 -> 문장 목록
    for s in sorted(doc["segments"], key=lambda s: s["start"]):
        sentences = collected.setdefault(display_name(doc["speakers"], s["speaker"]), [])
        for sentence in _SENTENCE_GAP.split(s["text"].strip()):
            if len(sentences) >= n:
                break
            if sentence:
                sentences.append(sentence)
    return {name: " ".join(sents)[:120] for name, sents in collected.items()}


def output_paths(input_path: str, cwd: str) -> tuple:
    src = Path(input_path).expanduser().resolve()
    out_dir = src.parent
    in_library = (Path.home() / "Library") in (out_dir, *out_dir.parents)
    if in_library or not os.access(out_dir, os.W_OK):
        out_dir = Path(cwd)
    stem = src.stem
    n = 1
    while True:
        name = stem if n == 1 else f"{stem} ({n})"
        json_path, md_path = out_dir / f"{name}.json", out_dir / f"{name}.md"
        if not json_path.exists() and not md_path.exists():
            return json_path, md_path
        n += 1


def save(doc: dict, json_path: Path, md_path: Path) -> None:
    Path(json_path).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(md_path).write_text(render_markdown(doc), encoding="utf-8")


def load(json_path: Path) -> dict:
    return json.loads(Path(json_path).read_text(encoding="utf-8"))
