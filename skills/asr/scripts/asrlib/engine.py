"""mlx-audio 래퍼. mlx 를 import 하는 유일한 모듈이며, import 는 메서드 안에서만 한다."""
import re

import numpy as np

from asrlib.audio import SR

MAX_TOKENS = 16384
RETRY_REPETITION_PENALTY = 1.1  # VibeVoice 이슈 #373 반복 루프 대응
TRUNCATION_MARGIN = 10.0  # 마지막 구간 끝이 조각 끝보다 이만큼 앞이면 끊김을 의심한다
MLX_CACHE_LIMIT_GB = 2  # MLX 가 풀어 둔 버퍼를 붙잡아 두는 캐시 상한. 피크 메모리를 줄인다.
SPEECH_AFTER_MARGIN = 1.0  # VAD 구간이 마지막 구간 끝보다 이만큼 넘게 이어지면 뒤에 발화가 있다고 본다

_TAG = re.compile(r"\[[^\]]*\]")


def _limit_cache() -> None:
    """모델을 처음 올릴 때 부른다. 여러 번 불러도 같다."""
    import mlx.core as mx
    mx.set_cache_limit(MLX_CACHE_LIMIT_GB * 1024**3)


class TranscriptionFailed(Exception):
    pass


def _parse_failure(raw_segments: list, text: str) -> str | None:
    if not raw_segments and _TAG.sub("", text or "").strip():
        return f"전사 결과를 구간으로 나누지 못했습니다: {text[:80]!r}"
    return None


def _truncation(raw_segments: list, duration: float, speech) -> str | None:
    """speech 는 VAD 구간을 돌려주는 함수다. 끊김이 의심될 때만 부른다."""
    last_end = max((float(s.get("end", 0.0)) for s in raw_segments), default=0.0)
    if last_end >= duration - TRUNCATION_MARGIN:
        return None
    if any(end > last_end + SPEECH_AFTER_MARGIN for _, end in speech()):
        return f"전사가 {last_end:.1f}초에서 끊겼습니다(조각 길이 {duration:.1f}초, 뒤에 발화 있음)"
    return None


class Engine:
    def __init__(self, asr_repo="mlx-community/VibeVoice-ASR-8bit",
                 vad_repo="mlx-community/silero-vad",
                 ecapa_repo="aufklarer/SpeechBrain-ECAPA-VoxCeleb-20M-MLX"):
        self.asr_repo = asr_repo
        self.vad_repo = vad_repo
        self.ecapa_repo = ecapa_repo
        self._asr = None
        self._vad = None
        self._ecapa = None

    def speech_intervals(self, audio: np.ndarray) -> list[tuple[float, float]]:
        if self._vad is None:
            from mlx_audio.vad import load
            _limit_cache()
            self._vad = load(self.vad_repo)
        stamps = self._vad.get_speech_timestamps(audio, sample_rate=SR, return_seconds=True)
        return [(float(s["start"]), float(s["end"])) for s in stamps]

    def _generate(self, audio: np.ndarray, **kwargs):
        if self._asr is None:
            from mlx_audio.stt import load
            _limit_cache()
            self._asr = load(self.asr_repo)
        return self._asr.generate(audio, sampling_rate=SR, max_tokens=MAX_TOKENS, **kwargs)

    def _check(self, result, audio: np.ndarray) -> str | None:
        raw = list(result.segments or [])
        return _parse_failure(raw, result.text) or _truncation(
            raw, len(audio) / SR, lambda: self.speech_intervals(audio))

    def transcribe(self, audio: np.ndarray) -> list[dict]:
        result = self._generate(audio)
        reason = self._check(result, audio)
        if reason:
            result = self._generate(audio, repetition_penalty=RETRY_REPETITION_PENALTY)
            reason = self._check(result, audio)
            if reason:
                raise TranscriptionFailed(reason)
        return [
            {"start": float(s["start"]), "end": float(s["end"]),
             "speaker_id": int(s["speaker_id"]), "text": s.get("text", "")}
            for s in result.segments
            if s.get("speaker_id") is not None and "start" in s and "end" in s
        ]

    def embed(self, audio: np.ndarray) -> np.ndarray:
        if self._ecapa is None:
            from mlx_audio.codec.models.ecapa_tdnn.speaker import EcapaSpeakerEmbeddingExtractor
            _limit_cache()
            self._ecapa = EcapaSpeakerEmbeddingExtractor.from_pretrained(self.ecapa_repo)
        v = np.asarray(self._ecapa.encode(audio), dtype=np.float32)
        return v / np.linalg.norm(v)
