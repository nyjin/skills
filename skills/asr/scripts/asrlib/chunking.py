"""긴 녹음을 묵음 지점에서 max_len 이하 조각으로 나눈다. 순수 파이썬."""
from dataclasses import dataclass

MIN_SILENCE = 0.3


@dataclass(frozen=True)
class Chunk:
    index: int
    start: float
    end: float


def _silence_midpoints(duration, speech):
    """발화 사이(맨 앞·맨 뒤 포함) 0.3초 이상 빈틈의 중점 목록."""
    mids = []
    prev_end = 0.0
    for s, e in list(speech) + [(duration, duration)]:
        s = min(s, duration)
        if s - prev_end >= MIN_SILENCE:
            mids.append((prev_end + s) / 2)
        prev_end = max(prev_end, min(e, duration))
    return mids


def plan_chunks(duration, speech, max_len=600.0, overlap=45.0, search=120.0):
    if max_len <= 2 * overlap:
        raise ValueError("max_len 은 overlap 의 2배보다 커야 합니다.")
    if duration <= max_len:
        return [Chunk(0, 0.0, duration)]
    mids = _silence_midpoints(duration, speech)
    chunks = []
    s = 0.0
    while True:
        limit = s + max_len
        if limit >= duration:
            chunks.append(Chunk(len(chunks), s, duration))
            return chunks
        # 다음 시작(cut - overlap)이 s 보다 앞서지 않도록 하한을 둔다.
        lo = max(limit - search, s + 2 * overlap)
        cands = [m for m in mids if lo <= m <= limit]
        cut = max(cands) if cands else limit
        chunks.append(Chunk(len(chunks), s, cut))
        s = cut - overlap


def keep_windows(chunks):
    """조각별로 최종 전사본에 남길 (start, end). 경계는 겹친 구간의 중점."""
    bounds = [0.0]
    for prev, nxt in zip(chunks, chunks[1:]):
        bounds.append((nxt.start + prev.end) / 2)
    bounds.append(chunks[-1].end)
    return list(zip(bounds, bounds[1:]))
