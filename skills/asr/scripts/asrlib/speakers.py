"""조각 안 화자 번호를 전역 명부(S1, S2, …)에 잇는다. 목소리 지문은 numpy 만 쓴다."""
from dataclasses import dataclass
from typing import Callable

import numpy as np

MIN_SEG_SEC = 2.0
TRIM_SEC = 0.25
MAX_PRINT_SEC = 60.0
LINK_THRESHOLD = 0.5
MERGE_THRESHOLD = 0.7
MIN_OVERLAP_VOTE_SEC = 3.0


@dataclass
class LocalSpeaker:
    local: int
    voiceprint: np.ndarray | None
    speech_sec: float
    first_start: float  # 원본 기준 초


def _normalize(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _cos(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def _to_list(v: np.ndarray) -> list[float]:
    return [round(float(x), 6) for x in v]


def _num(sid: str) -> int:
    return int(sid[1:])


def local_speakers(segs: list[dict], audio: np.ndarray, sr: int, offset: float,
                   embed: Callable[[np.ndarray], np.ndarray]) -> dict[int, LocalSpeaker]:
    by_local: dict[int, list[dict]] = {}
    for s in segs:
        by_local.setdefault(s["speaker_id"], []).append(s)

    result = {}
    for local, items in by_local.items():
        speech_sec = sum(s["end"] - s["start"] for s in items)
        first_start = offset + min(s["start"] for s in items)

        usable = [s for s in items if s["end"] - s["start"] >= MIN_SEG_SEC]
        usable.sort(key=lambda s: s["end"] - s["start"], reverse=True)
        embs, weights, total = [], [], 0.0
        for s in usable:
            a = int(round((s["start"] + TRIM_SEC) * sr))
            b = int(round((s["end"] - TRIM_SEC) * sr))
            dur = (b - a) / sr
            if total + dur > MAX_PRINT_SEC:
                if total > 0:
                    break
                # 한 구간이 상한보다 길면 그 구간만 상한 길이로 잘라 쓴다.
                b = a + int(MAX_PRINT_SEC * sr)
                dur = (b - a) / sr
            embs.append(np.asarray(embed(audio[a:b]), dtype=float))
            weights.append(dur)
            total += dur

        print_ = None
        if embs:
            print_ = _normalize(np.average(np.stack(embs), axis=0, weights=weights))
        result[local] = LocalSpeaker(local, print_, float(speech_sec), float(first_start))
    return result


def overlap_votes(prev: list[dict], nxt: list[dict],
                  window: tuple[float, float]) -> dict[int, str]:
    w0, w1 = window
    totals: dict[tuple[int, str], float] = {}
    for p in prev:
        for n in nxt:
            sec = min(p["end"], n["end"], w1) - max(p["start"], n["start"], w0)
            if sec > 0:
                key = (n["speaker_id"], p["speaker"])
                totals[key] = totals.get(key, 0.0) + sec

    best_global: dict[int, tuple[float, str]] = {}
    best_local: dict[str, tuple[float, int]] = {}
    for (local, sid), sec in totals.items():
        if local not in best_global or sec > best_global[local][0]:
            best_global[local] = (sec, sid)
        if sid not in best_local or sec > best_local[sid][0]:
            best_local[sid] = (sec, local)

    return {
        local: sid for local, (sec, sid) in best_global.items()
        if best_local[sid][1] == local and sec >= MIN_OVERLAP_VOTE_SEC
    }


def _absorb(entry: dict, speech_sec: float, first_seen: float, print_) -> None:
    """명부 항목에 다른 화자의 발화량·첫 등장·지문을 합친다."""
    if print_ is not None:
        if entry["voiceprint"] is None:
            entry["voiceprint"] = _to_list(_normalize(np.asarray(print_, dtype=float)))
        else:
            mixed = np.average(
                np.stack([np.asarray(entry["voiceprint"], dtype=float),
                          np.asarray(print_, dtype=float)]),
                axis=0, weights=[entry["speech_sec"], speech_sec])
            entry["voiceprint"] = _to_list(_normalize(mixed))
    entry["speech_sec"] = float(entry["speech_sec"] + speech_sec)
    entry["first_seen"] = float(min(entry["first_seen"], first_seen))


def _new_entry(speakers: dict, ls: LocalSpeaker) -> str:
    sid = f"S{max((_num(k) for k in speakers), default=0) + 1}"
    speakers[sid] = {
        "name": None,
        "first_seen": float(ls.first_start),
        "speech_sec": float(ls.speech_sec),
        "voiceprint": None if ls.voiceprint is None else _to_list(_normalize(ls.voiceprint)),
    }
    return sid


def link_chunk(speakers: dict, links: list, chunk_index: int,
               locals_: dict[int, LocalSpeaker], votes: dict[int, str]) -> dict[int, str]:
    order = sorted(locals_.values(), key=lambda ls: (ls.first_start, ls.local))
    mapping: dict[int, str] = {}

    def record(ls, sid, how, sim=None):
        link = {"chunk": chunk_index, "local": ls.local, "speaker": sid, "how": how}
        if sim is not None:
            link["similarity"] = round(float(sim), 4)
        links.append(link)
        mapping[ls.local] = sid

    if not speakers:  # 명부가 비었으면 이 조각이 seed 다.
        for ls in order:
            record(ls, _new_entry(speakers, ls), "seed")
        return mapping

    # 1. 겹침 구간 투표
    for ls in order:
        sid = votes.get(ls.local)
        if sid is None:
            continue
        roster_print = speakers[sid]["voiceprint"]
        sim = None
        if ls.voiceprint is not None and roster_print is not None:
            sim = _cos(ls.voiceprint, roster_print)
        _absorb(speakers[sid], ls.speech_sec, ls.first_start, ls.voiceprint)
        record(ls, sid, "overlap", sim)

    # 2. 지문 유사도로 탐욕적 일대일 배정
    used = set(mapping.values())
    rest = [ls for ls in order if ls.local not in mapping]
    cands = [sid for sid, e in speakers.items()
             if e["voiceprint"] is not None and "merged_into" not in e and sid not in used]
    sims = {
        (ls.local, sid): _cos(ls.voiceprint, speakers[sid]["voiceprint"])
        for ls in rest if ls.voiceprint is not None for sid in cands
    }
    by_local = {ls.local: ls for ls in rest}
    for (local, sid), sim in sorted(sims.items(), key=lambda kv: kv[1], reverse=True):
        if sim < LINK_THRESHOLD:
            break
        if local in mapping or sid in used:
            continue
        ls = by_local[local]
        _absorb(speakers[sid], ls.speech_sec, ls.first_start, ls.voiceprint)
        used.add(sid)
        record(ls, sid, "voice", sim)

    # 3. 남은 화자는 새 ID
    for ls in rest:
        if ls.local in mapping:
            continue
        if ls.voiceprint is None:
            record(ls, _new_entry(speakers, ls), "new-noprint")
            continue
        row = [sim for (local, _), sim in sims.items() if local == ls.local]
        record(ls, _new_entry(speakers, ls), "new", max(row) if row else None)
    return mapping


def consolidate(speakers: dict) -> list[tuple[str, str, float]]:
    merged = []
    while True:
        alive = [sid for sid, e in speakers.items()
                 if "merged_into" not in e and e["voiceprint"] is not None]
        best = None
        for i, a in enumerate(alive):
            for b in alive[i + 1:]:
                sim = _cos(speakers[a]["voiceprint"], speakers[b]["voiceprint"])
                if best is None or sim > best[0]:
                    best = (sim, a, b)
        if best is None or best[0] < MERGE_THRESHOLD:
            return merged
        sim, a, b = best
        into, frm = sorted((a, b), key=lambda s: (speakers[s]["first_seen"], _num(s)))
        loser = speakers[frm]
        _absorb(speakers[into], loser["speech_sec"], loser["first_seen"], loser["voiceprint"])
        sim = round(sim, 4)
        loser["merged_into"] = into
        loser["merge_similarity"] = sim
        loser.pop("label_no", None)
        merged.append((frm, into, sim))


def assign_labels(speakers: dict) -> None:
    alive = sorted((sid for sid, e in speakers.items() if "merged_into" not in e),
                   key=lambda s: (speakers[s]["first_seen"], _num(s)))
    for no, sid in enumerate(alive, start=1):
        speakers[sid]["label_no"] = no
    for e in speakers.values():
        if "merged_into" in e:
            e.pop("label_no", None)
