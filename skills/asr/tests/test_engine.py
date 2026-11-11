from types import SimpleNamespace

import numpy as np
import pytest

from asrlib.engine import Engine, TranscriptionFailed, _parse_failure, _truncation


def seg(start, end, speaker_id=0, text="안녕하세요"):
    return {"start": start, "end": end, "speaker_id": speaker_id, "text": text}


def no_vad():
    raise AssertionError("VAD 를 부르면 안 된다")


def test_parse_failure_on_untagged_text():
    assert _parse_failure([], '[{"Start": 0, "End": 1, "Speak') is not None


def test_parse_failure_ignores_tag_only_text():
    assert _parse_failure([], "[Silence] [Environmental Sounds]") is None


def test_truncation_with_later_speech():
    assert _truncation([seg(0, 100)], 600.0, lambda: [(0, 100), (300, 320)]) is not None


def test_truncation_with_interval_spanning_last_end():
    assert _truncation([seg(0, 100)], 600.0, lambda: [(50, 400)]) is not None


def test_no_truncation_when_vad_only_slightly_overhangs():
    assert _truncation([seg(0, 100)], 600.0, lambda: [(50, 100.5)]) is None


def test_no_truncation_near_end_skips_vad():
    assert _truncation([seg(0, 590)], 600.0, no_vad) is None


class FakeASR:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def generate(self, audio, **kwargs):
        self.calls.append(kwargs)
        return self.results.pop(0)


BAD = SimpleNamespace(segments=[], text="깨진 출력")
GOOD = SimpleNamespace(
    segments=[seg(0, 2, 0), seg(2, 3, None, "[Silence]"), {"speaker_id": 1, "text": "x"},
              seg(3, 5, 1, "물류센터")],
    text="...",
)


def engine_with(results):
    e = Engine()
    e._asr = FakeASR(results)
    return e


AUDIO = np.zeros(16000 * 10, dtype=np.float32)


def test_transcribe_retries_with_repetition_penalty():
    e = engine_with([BAD, GOOD])
    segs = e.transcribe(AUDIO)
    assert "repetition_penalty" not in e._asr.calls[0]
    assert e._asr.calls[1]["repetition_penalty"] == 1.1
    assert segs == [
        {"start": 0.0, "end": 2.0, "speaker_id": 0, "text": "안녕하세요"},
        {"start": 3.0, "end": 5.0, "speaker_id": 1, "text": "물류센터"},
    ]


def test_transcribe_raises_after_second_failure():
    e = engine_with([BAD, BAD])
    with pytest.raises(TranscriptionFailed):
        e.transcribe(AUDIO)
    assert len(e._asr.calls) == 2


def test_transcribe_first_success_does_not_retry():
    e = engine_with([GOOD])
    assert len(e.transcribe(AUDIO)) == 2
    assert len(e._asr.calls) == 1
