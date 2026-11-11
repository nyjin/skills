"""녹음 파일을 화자별 전사본(.json + .md)으로 만든다.

    python transcribe.py <audio> [--cwd DIR]

요약 JSON 은 stdout, 진행 상황은 stderr 로 낸다. 종료 코드: 성공 0, 입력 오류 2.
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asrlib.audio import ENHANCE_FILTERS, SR, AudioError, decode, integrated_lufs, needs_enhancement  # noqa: E402
from asrlib.chunking import keep_windows, plan_chunks  # noqa: E402
from asrlib.engine import Engine, TranscriptionFailed  # noqa: E402
from asrlib.speakers import assign_labels, consolidate, link_chunk, local_speakers, overlap_votes  # noqa: E402
from asrlib.transcript import _ts, display_name, first_lines, output_paths, save  # noqa: E402

SILENCE_LUFS = -70.0  # ebur128 절대 게이트. 이 값이면 게이트를 넘은 구간이 없다(무음).


def _kept(segments: list[dict], ok_chunks: list) -> list[dict]:
    """겹친 구간의 중복을 뺀다. 시작 시각이 자기 조각의 남길 구간 [s, e) 에 든 발화만 남긴다.

    실패한 조각은 빼고 경계를 잡아, 이웃 조각이 덮은 발화를 잃지 않는다.
    """
    if not ok_chunks:
        return []
    windows = dict(zip((c.index for c in ok_chunks), keep_windows(ok_chunks)))
    last = ok_chunks[-1].index
    kept = []
    for seg in segments:
        s, e = windows[seg["chunk"]]
        if s <= seg["start"] < e or (seg["chunk"] == last and seg["start"] == e):
            kept.append(seg)
    return kept


def _recount(speakers: dict, kept: list[dict]) -> None:
    """발화량과 첫 등장을 중복 제거 뒤 남은 발화로 다시 센다. 첫 등장은 늦추지 않는다."""
    by_sid: dict[str, list[dict]] = {}
    for seg in kept:
        by_sid.setdefault(seg["speaker"], []).append(seg)
    for sid, segs in by_sid.items():
        entry = speakers[sid]
        entry["speech_sec"] = sum(s["end"] - s["start"] for s in segs)
        entry["first_seen"] = min(entry["first_seen"], min(s["start"] for s in segs))


def run(input_path: str, cwd: str, engine) -> dict:
    if not os.path.isfile(input_path):
        raise AudioError(f"파일을 찾을 수 없습니다: {input_path}")

    # ③ 음질 판정. 무음(-70 이하·-inf)은 보정할 것이 없다.
    lufs = integrated_lufs(input_path)
    enhanced = None
    if math.isfinite(lufs) and lufs > SILENCE_LUFS and needs_enhancement(lufs):
        enhanced = {"lufs_before": round(lufs, 1), "filters": ENHANCE_FILTERS}
        audio = decode(input_path, ENHANCE_FILTERS)
    else:
        audio = decode(input_path)
    duration = len(audio) / SR

    # ④ 조각 나누기
    chunks = plan_chunks(duration, engine.speech_intervals(audio))

    # ⑤ 조각별 전사와 화자 연결
    speakers: dict = {}
    links: list = []
    segments: list[dict] = []  # 원본 기준 시각, 전역 ID
    failed_ranges: list[dict] = []
    ok_chunks = []
    prev_ok = False
    for chunk in chunks:
        print(f"조각 {chunk.index + 1}/{len(chunks)} 처리 중 "
              f"({_ts(chunk.start)}–{_ts(chunk.end)})", file=sys.stderr, flush=True)
        chunk_audio = audio[int(round(chunk.start * SR)):int(round(chunk.end * SR))]
        try:
            segs = engine.transcribe(chunk_audio)
        except TranscriptionFailed as e:
            failed_ranges.append({"start": chunk.start, "end": chunk.end, "reason": str(e)})
            prev_ok = False
            continue

        locals_ = local_speakers(segs, chunk_audio, SR, chunk.start, engine.embed)
        absolute = [{**s, "start": s["start"] + chunk.start, "end": s["end"] + chunk.start}
                    for s in segs]
        votes = {}
        if prev_ok:
            window = (chunk.start, chunks[chunk.index - 1].end)
            prev = [g for g in segments if g["end"] > window[0] and g["start"] < window[1]]
            votes = overlap_votes(prev, absolute, window)
        mapping = link_chunk(speakers, links, chunk.index, locals_, votes)

        segments += [{"start": s["start"], "end": s["end"], "speaker": mapping[s["speaker_id"]],
                      "text": s["text"], "chunk": chunk.index} for s in absolute]
        ok_chunks.append(chunk)
        prev_ok = True

    # 겹침 중복 제거 → 발화량 재계산 → ⑥ 통합·번호
    kept = sorted(_kept(segments, ok_chunks), key=lambda s: s["start"])
    _recount(speakers, kept)
    merged = consolidate(speakers)
    assign_labels(speakers)
    for entry in speakers.values():
        entry["first_seen"] = round(entry["first_seen"], 3)
        entry["speech_sec"] = round(entry["speech_sec"], 3)

    # ⑦ 저장
    doc = {
        "source": {"file": os.path.basename(input_path), "duration": round(duration, 3)},
        "enhanced": enhanced,
        "chunks": [{"index": c.index, "start": round(c.start, 3), "end": round(c.end, 3)}
                   for c in chunks],
        "speakers": speakers,
        "links": links,
        "segments": [{"start": round(s["start"], 3), "end": round(s["end"], 3),
                      "speaker": s["speaker"], "text": s["text"]} for s in kept],
        "failed_ranges": [{**f, "start": round(f["start"], 3), "end": round(f["end"], 3)}
                          for f in failed_ranges],
    }
    json_path, md_path = output_paths(input_path, cwd)
    save(doc, json_path, md_path)

    # ⑧ 요약
    lines = first_lines(doc)
    alive = sorted((sid for sid, e in speakers.items() if "merged_into" not in e),
                   key=lambda sid: speakers[sid]["label_no"])
    labels = [display_name(speakers, sid) for sid in alive]
    return {
        "json": str(json_path),
        "md": str(md_path),
        "chunks": len(chunks),
        "speakers": [{"label": label, "first_lines": lines.get(label, "")} for label in labels],
        "enhanced": None if enhanced is None else {"lufs_before": enhanced["lufs_before"]},
        "merged": [{"from_label": frm, "into_label": display_name(speakers, into),
                    "similarity": round(sim, 2)} for frm, into, sim in merged],
        "failed_ranges": doc["failed_ranges"],
        "no_speech": not kept,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="녹음 파일을 화자별 전사본으로 만든다.")
    parser.add_argument("audio")
    parser.add_argument("--cwd", default=os.getcwd(),
                        help="입력 폴더에 저장할 수 없을 때 결과를 둘 폴더")
    args = parser.parse_args(argv)

    try:
        summary = run(args.audio, args.cwd, Engine())
    except AudioError as e:
        print(e, file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
