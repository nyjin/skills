# 환경 점검과 설치

전사를 시작하기 전에 항상 `scripts/check_env.sh` 를 먼저 실행한다. 출력은 `KEY=VALUE` 줄이고 마지막 줄이 `STATUS` 다.

## 1. check_env 결과 해석

| STATUS | 종료 코드 | 할 일 |
|---|---|---|
| `ready` | 0 | 설치를 건너뛰고 전사로 진행한다. |
| `missing` | 10 | 아래 3~4절 순서로 설치를 안내하고 동의를 받는다. |
| `unsupported` | 20 | 6절 문구를 보여 주고 멈춘다. 설치하지 않는다. |

`missing` 일 때 빠진 항목은 `FFMPEG`, `PYTHON`, `VENV`, `MLX_AUDIO`, `MODEL_VIBEVOICE`, `MODEL_VAD`, `MODEL_ECAPA` 키로 알 수 있다. `MLX_AUDIO` 는 import 가 안 되면 `absent`, 0.6.2 보다 낮으면 그 버전 값이며 둘 다 빠진 것으로 본다. `install.sh` 가 같은 출력을 읽어 빠진 것만 설치한다.

## 2. 진행 순서

1. `bash scripts/install.sh --dry-run` 을 실행해 할 일 목록과 예상 용량을 얻는다. 아무것도 설치하지 않는다.
2. 그 결과로 3절의 고지 문안을 채워 사용자에게 보여 준다.
3. 4절대로 동의를 받는다.
4. 동의하면 `bash scripts/install.sh` 를 실행한다. 진행 상황은 `STEP <n>/<total> <설명>` 으로 나온다.
5. 끝나면 `install.sh` 가 `check_env.sh` 를 다시 실행한다. `STATUS=ready` 가 아니면 exit 1 이므로 출력을 그대로 보여 준다.

`install.sh` 종료 코드는 다음과 같다.

| 코드 | 뜻 |
|---|---|
| 0 | 설치 완료 (또는 할 일 없음) |
| 1 | 단계 실패, 또는 설치 후에도 ready 가 아님 |
| 2 | Homebrew 없음 (7절) |
| 3 | 디스크 부족 (여유가 예상 용량 + 2GB 보다 작음) |
| 20 | 미지원 환경 |

## 3. 고지 문안 템플릿

`<>` 안은 `--dry-run` 출력과 `check_env` 값으로 채운다. 빠진 항목만 적는다. `[...]` 는 해당할 때만 넣는다. `FFMPEG=absent` 나 `PYTHON=absent` 면 Homebrew 설치 줄을 반드시 넣는다. `--dry-run` 의 예상 용량 합계는 Homebrew 패키지를 뺀 값이다.

```
녹음을 화자별로 나눠 전사하려면 아래 항목을 이 Mac에 설치해야 합니다.

- 설치 항목: <빠진 항목 목록. 예: 가상환경, mlx-audio 0.6.5, 전사 모델 VibeVoice-ASR-8bit, VAD 모델, 목소리 지문 모델>
[ffmpeg·Python 3.12 가 없을 때] - Homebrew 로 설치: <ffmpeg, python@3.12 중 빠진 것> (`brew install ffmpeg`, `brew install python@3.12`)
- 예상 용량: 약 <합계>GB (디스크 여유 <DISK_FREE_GB>GB)[Homebrew 설치가 있을 때] — Homebrew 패키지 용량은 합계에 넣지 않았습니다
- 설치 위치: 가상환경 <venv 경로, 기본 ~/.local/share/asr/venv>, 모델 <HF 캐시, 기본 ~/.cache/huggingface/hub>
- 제공 기능:
  - 한국어 받아쓰기, 화자분리, 타임스탬프
  - 긴 녹음 처리와 조각 간 화자 연결
  - Apple Silicon GPU 가속
- 소요 시간: 모델 다운로드가 대부분입니다. 실측 약 6MB/s, 9.5GB 기준 수십 분입니다. 이미 받은 모델이 있으면 그만큼 줄어듭니다.

설치할까요?
```

## 4. 동의를 받는 방법

- 위 고지를 보여 준 뒤 사용자의 명시적인 답을 기다린다. 침묵이나 "녹음 전사해 줘" 같은 원래 요청만으로는 동의로 보지 않는다.
- "네", "설치해" 처럼 분명한 긍정만 동의다.
- 거절하거나 답이 모호하면 설치하지 않고 이후 단계로 가지 않는다. 다음 문구로 끝낸다.

  > 설치 없이는 전사할 수 없어 여기서 멈춥니다. 설치할 준비가 되면 다시 요청해 주세요.

## 5. 설치 중 확인할 점

- 진행 상황 줄(`STEP n/total`)을 사용자에게 그대로 알려 준다. 모델 다운로드는 오래 걸린다.
- 다운로드가 끊겨 `.incomplete` 파일이 남으면 `check_env` 가 그 모델을 `missing` 으로 보고한다. `install.sh` 를 다시 실행하면 이어서 받는다.
- `ASR_VENV`, `ASR_HF_HUB` 로 위치를 바꿀 수 있다. `check_env.sh` 와 `install.sh` 가 같은 값을 쓴다.

## 6. 미지원 환경 안내

`STATUS=unsupported` 면 원인에 따라 아래 문구 하나를 보여 주고 멈춘다. `install.sh` 는 이 환경에서 아무것도 설치하지 않고 exit 20 한다.

- OS 나 칩이 원인이면(`UNSUPPORTED_REASON` 이 macOS·Apple Silicon 이야기):

  > 이 스킬은 Apple Silicon(M1 이상) Mac 전용입니다. 현재 환경(<OS>/<ARCH>)은 지원하지 않습니다.

  `<OS>`, `<ARCH>` 는 `check_env` 출력의 `OS=`, `ARCH=` 값이다.
- 메모리가 원인이면 `UNSUPPORTED_REASON=` 의 값만 보여 준다(`메모리 <N>GB — 10분 조각 처리에 최소 24GB가 필요합니다.`). Apple Silicon 문구는 붙이지 않는다.

## 7. Homebrew 가 없을 때

ffmpeg 나 Python 3.12 이상이 필요한데 `BREW=absent` 이면 설치하지 않고 아래 문구를 보여 준다. `install.sh` 는 이 문구를 출력하고 exit 2 한다.

> Homebrew가 없어 자동 설치할 수 없습니다. https://brew.sh 에서 설치한 뒤 다시 실행해 주세요.
