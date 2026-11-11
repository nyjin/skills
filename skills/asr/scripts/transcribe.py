"""녹음 파일을 화자별 전사본(.json + .md)으로 만든다.

    python transcribe.py <audio> [--cwd DIR]

요약 JSON 은 stdout, 진행 상황은 stderr 로 낸다.
종료 코드: 성공 0, 입력·저장 오류 2, 메모리 부족 등 엔진 오류 3.
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asrlib.audio import ENHANCE_FILTERS, SR, AudioError, decode_mapped, integrated_lufs, needs_enhancement  # noqa: E402
from asrlib.chunking import keep_windows, plan_chunks  # noqa: E402
from asrlib.engine import Engine, TranscriptionFailed  # noqa: E402
from asrlib.speakers import assign_labels, consolidate, link_chunk, local_speakers, overlap_votes  # noqa: E402
from asrlib.transcript import _ts, display_name, first_lines, output_paths, save  # noqa: E402

SILENCE_LUFS = -70.0  # ebur128 절대 게이트. 이 값이면 게이트를 넘은 구간이 없다(무음).
STRADDLE_SEC = 0.5  # 뒤 조각 발화가 앞 조각이 남긴 끝보다 이만큼 넘게 이어지면 남긴다
VAD_WINDOW_SEC = 600.0  # VAD 에 한 번에 넘기는 길이. 녹음 전체를 한꺼번에 MLX 에 올리지 않는다.
VAD_JOIN_SEC = 1.0  # 창 경계에서 이 안으로 맞닿은 앞뒤 발화 구간은 하나로 잇는다.
# 새 창은 VAD 상태가 처음부터 시작해 이어지던 발화를 수백 ms 늦게 잡을 수 있다. 그 틈이 가짜 무음(자를 자리)이 되지 않게 한다.


def _speech_intervals(engine, audio) -> list[tuple[float, float]]:
    """녹음을 VAD_WINDOW_SEC 창으로 나눠 VAD 를 돌리고 원본 기준 구간으로 모은다.

    창 경계에 걸친 발화는 양쪽 창에서 경계에 닿은 두 구간으로 나오므로 다시 잇는다.
    """
    step = int(VAD_WINDOW_SEC * SR)
    out: list[tuple[float, float]] = []
    for a in range(0, len(audio), step):
        edge = a / SR
        for i, (s, e) in enumerate(engine.speech_intervals(np.array(audio[a:a + step]))):
            s, e = s + edge, e + edge
            if (i == 0 and out and out[-1][1] >= edge - VAD_JOIN_SEC
                    and s <= edge + VAD_JOIN_SEC):
                out[-1] = (out[-1][0], max(out[-1][1], e))
            else:
                out.append((s, e))
    return out


def _kept(segments: list[dict], ok_chunks: list) -> list[dict]:
    """겹친 구간의 중복을 뺀다. 빠뜨리느니 중복을 남긴다.

    경계 b 는 이웃한 성공 조각 사이 겹침의 중점이다(실패한 조각은 빼고 잡는다).
    앞 조각은 b 앞에서 시작한 발화를 남긴다. 뒤 조각은 b 이후에 시작한 발화와,
    b 앞에서 시작했어도 앞 조각이 남긴 발화의 끝(E)보다 STRADDLE_SEC 넘게 이어지는
    발화를 남긴다. 두 조각의 시각이 조금 어긋나거나 구간을 다르게 나눠도 잃지 않는다.
    """
    if not ok_chunks:
        return []
    windows = keep_windows(ok_chunks)
    by_chunk: dict[int, list[dict]] = {}
    for seg in segments:
        by_chunk.setdefault(seg["chunk"], []).append(seg)
    kept = []
    prev_end = 0.0  # 앞 조각이 남긴 발화의 가장 늦은 끝(E). 없으면 0.
    for i, (chunk, (s, e)) in enumerate(zip(ok_chunks, windows)):
        last = i == len(ok_chunks) - 1
        mine = [seg for seg in by_chunk.get(chunk.index, [])
                if (seg["start"] >= s or seg["end"] > prev_end + STRADDLE_SEC)
                and (seg["start"] < e or (last and seg["start"] == e))]
        kept += mine
        prev_end = max((seg["end"] for seg in mine), default=0.0)
    return kept


def _recount(speakers: dict, kept: list[dict]) -> None:
    """발화량과 첫 등장을 중복 제거 뒤 남은 발화로 다시 센다. 첫 등장은 늦추지 않는다.

    남은 발화가 없는 화자는 명부에서 지운다(번호·통합 대상에서 빠지고 지문도 남기지 않는다).
    links 의 연결 기록은 그대로 둔다.
    """
    by_sid: dict[str, list[dict]] = {}
    for seg in kept:
        by_sid.setdefault(seg["speaker"], []).append(seg)
    for sid, segs in by_sid.items():
        entry = speakers[sid]
        entry["speech_sec"] = sum(s["end"] - s["start"] for s in segs)
        entry["first_seen"] = min(entry["first_seen"], min(s["start"] for s in segs))
    for sid in [sid for sid in speakers if sid not in by_sid]:
        del speakers[sid]


def _log_memory(engine, where: str) -> None:
    """메모리 사용량을 진행 로그에 남기고, 다음 구간의 피크를 새로 재도록 피크를 되돌린다."""
    report = engine.memory_report(reset_peak=True)
    if report:
        print(f"  메모리({where}, GiB) {report}", file=sys.stderr, flush=True)


def run(input_path: str, cwd: str, engine) -> dict:
    if not os.path.isfile(input_path):
        raise AudioError(f"파일을 찾을 수 없습니다: {input_path}")
    # 전사에 몇 분씩 쓰기 전에 저장할 폴더부터 확인한다.
    json_path, md_path = output_paths(input_path, cwd)
    out_dir = json_path.parent
    if not (out_dir.is_dir() and os.access(out_dir, os.W_OK)):
        raise AudioError(f"결과를 저장할 폴더에 쓸 수 없습니다: {out_dir}. "
                         "--cwd 로 쓸 수 있는 폴더를 지정해 주세요.")

    # ③ 음질 판정. 무음(-70 이하·-inf)은 보정할 것이 없다.
    lufs = integrated_lufs(input_path)
    enhanced = None
    if math.isfinite(lufs) and lufs > SILENCE_LUFS and needs_enhancement(lufs):
        enhanced = {"lufs_before": round(lufs, 1), "filters": ENHANCE_FILTERS}
        audio = decode_mapped(input_path, ENHANCE_FILTERS)
    else:
        audio = decode_mapped(input_path)
    duration = len(audio) / SR

    # ④ 조각 나누기
    chunks = plan_chunks(duration, _speech_intervals(engine, audio))

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
        engine.release()  # 앞 단계(VAD·앞 조각)가 남긴 캐시를 놓고 같은 기준선에서 시작한다
        _log_memory(engine, f"조각 {chunk.index + 1} 시작")
        chunk_audio = np.array(audio[int(round(chunk.start * SR)):int(round(chunk.end * SR))])
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
        _log_memory(engine, f"조각 {chunk.index + 1} 끝")

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
    try:
        save(doc, json_path, md_path)
    except OSError as e:
        raise AudioError(f"결과를 저장하지 못했습니다({json_path}): {e}") from e

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
    except (RuntimeError, MemoryError) as e:  # MLX/Metal 메모리 할당 실패는 RuntimeError 로 온다
        print("전사 엔진이 멈췄습니다. 메모리가 부족했을 수 있습니다. 다른 앱을 닫고 다시 실행해 주세요. "
              "그래도 같으면 이 Mac 의 메모리가 요구 사양보다 작은 것입니다.\n"
              f"원인: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
