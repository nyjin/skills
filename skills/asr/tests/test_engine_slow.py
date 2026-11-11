import numpy as np
import pytest
from conftest import FIXTURE

from asrlib.audio import decode
from asrlib.engine import Engine

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def engine():
    return Engine()


@pytest.fixture(scope="module")
def audio():
    return decode(str(FIXTURE))


def test_vad_on_fixture(engine, audio):
    intervals = engine.speech_intervals(audio)
    assert len(intervals) >= 1
    assert intervals[0][0] < 0.5
    assert intervals[-1][1] > 21


def test_transcribe_fixture(engine, audio):
    segs = engine.transcribe(audio)
    assert len(segs) >= 4
    assert all(isinstance(s["speaker_id"], int) for s in segs)
    assert "물류센터" in "".join(s["text"] for s in segs)


def test_embed_unit_norm(engine, audio):
    v = engine.embed(audio)
    assert v.shape == (192,)
    assert abs(np.linalg.norm(v) - 1) < 1e-3
