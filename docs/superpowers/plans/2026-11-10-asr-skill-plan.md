# `asr` 스킬 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 녹음 파일을 VibeVoice-ASR(MLX)로 화자별 한국어 전사본(.json 기록 + .md 보기용)으로 만들고, 화자 이름 바꾸기를 지원하는 Claude Code 스킬 `asr` 를 만든다.

**Architecture:**
- 순수 로직과 MLX 래퍼를 나눈다.
  - 순수 로직은 numpy 만 쓰는 모듈 4개다: 조각 나누기, 화자 연결·통합, 전사본 직렬화·렌더링, 오디오 측정·보정.
  - MLX 래퍼(`engine.py`)는 VibeVoice·Silero VAD·ECAPA 를 감싼다.
- CLI 2개(`transcribe.py`, `rename.py`)가 이 모듈들을 조립한다.
- 엔진은 의존성 주입을 받으므로, 오케스트레이션은 가짜 엔진으로 테스트한다.
- 설치는 셸 스크립트 2개(`check_env.sh`, `install.sh`)와 `references/install.md` 가 맡는다.

**Tech Stack:**
- 런타임: Python 3.12, mlx-audio 0.6.5(VibeVoice-ASR 8bit, Silero VAD, SpeechBrain ECAPA), numpy
- 외부 도구: ffmpeg, bash
- 테스트: pytest

**Spec:** `docs/superpowers/specs/2026-11-10-asr-skill-design.md` (Task 1 에서 저장소로 옮김)

## Global Constraints

**환경과 경로**
- 저장소는 `~/src/skills`, 스킬 경로는 `skills/asr/`, 설치 링크는 `~/.claude/skills/asr → ~/src/skills/skills/asr` 다.
- 지원 환경은 macOS + Apple Silicon 뿐이다. 그 밖에서는 안내 문구를 내고 멈춘다.
  - 안내 문구: `이 스킬은 Apple Silicon(M1 이상) Mac 전용입니다. 현재 환경(<os>/<arch>)은 지원하지 않습니다.`
- 실행 환경 venv 는 `~/.local/share/asr/venv`, 모델은 HF 기본 캐시(`~/.cache/huggingface/hub`)에 둔다.

**패키지와 모델**
- 패키지는 `mlx-audio==0.6.5` 로 고정한다(설계 최소 0.6.2, 검증 0.6.5).
- 모델은 세 개다.
  - 전사: `mlx-community/VibeVoice-ASR-8bit`
  - VAD: `mlx-community/silero-vad`
  - 목소리 지문: `aufklarer/SpeechBrain-ECAPA-VoxCeleb-20M-MLX` (`model.safetensors` 만)
- Python 은 시스템의 3.12 이상을 쓴다. 없으면 `brew install python@3.12` 를 쓰고, Homebrew 가 없으면 안내 후 멈춘다.

**동작 규칙**
- 원본 녹음 파일에는 읽기 외 접근을 하지 않는다. 보정은 ffmpeg 출력 배열에만 한다.
- 설치는 사용자 동의 뒤에만 실행한다. 거절하면 이후 단계로 가지 않는다.
- 조각은 겹침 포함 **최대 600초(10분)** 다.
- 수치는 기본값이며 조정할 수 있다.
  - 겹침 45초, 연결 기준 0.5, 통합 기준 0.7
  - 보정 임계 -40 LUFS
- 목소리 지문은 해당 녹음의 `.json` 안에만 저장한다.

**표시와 커밋**
- 화자 표시 이름은 `name` 이 있으면 그 이름, 없으면 `화자 {label_no}` 다.
- 전사본 줄 형식은 `**[HH:MM:SS] <표시이름>:** <텍스트>` 이고, 단락 사이는 빈 줄 하나다.
- 커밋 메시지는 이 저장소 관례대로 한국어 `feat: …` / `test: …` / `docs: …` 형식이다.

## Review Focus

1. **경로에 공백·한글이 있는 입력**(음성 메모 `20261106 164354.m4a`): ffmpeg 호출이 깨지지 않아야 한다. → Task 3 `test_decode_path_with_space`
2. **말이 전혀 없는 녹음**(VibeVoice 가 `[Silence]` 만 반환): 빈 전사본과 "인식된 발화가 없습니다" 요약을 내고, 죽지 않는다. → Task 8 `test_all_silence`
3. **쓰기 불가 위치나 음성 메모 폴더의 입력, 같은 이름의 결과가 이미 있는 경우**: 현재 폴더로 저장하고, 기존 파일을 덮어쓰지 않는다. → Task 5 `test_output_paths_*`
4. **조각 안에서 2초 미만 발화만 한 화자**: 지문이 없어도 죽지 않고 새 화자로 등록한다. → Task 6 `test_link_speaker_without_print`
5. **다운로드가 중간에 끊긴 모델**(`.incomplete` 잔존): `check_env` 가 missing 으로 보고한다. → Task 1 `incomplete model is missing`

---

## 파일 구조

```
skills/asr/
├── SKILL.md                     # 트리거 description, 흐름, 안내 문구        (Task 10)
├── references/install.md        # 환경 게이트·설치 고지·절차                 (Task 2)
├── scripts/
│   ├── check_env.sh             # 점검. KEY=VALUE 출력                        (Task 1)
│   ├── install.sh               # 설치 실행                                    (Task 2)
│   ├── transcribe.py            # CLI: 전사 오케스트레이션                    (Task 8)
│   ├── rename.py                # CLI: 이름 바꾸기                             (Task 9)
│   └── asrlib/
│       ├── __init__.py
│       ├── audio.py             # 디코딩·LUFS·보정                            (Task 3)
│       ├── chunking.py          # 조각 계획·중복 제거 경계                    (Task 4)
│       ├── transcript.py        # 문서 모델·md 렌더·출력 경로                 (Task 5)
│       ├── speakers.py          # 지문 집계·겹침 투표·연결·통합·번호          (Task 6)
│       └── engine.py            # mlx-audio 래퍼 (유일하게 mlx import)        (Task 7)
└── tests/
    ├── conftest.py              # scripts/ 를 sys.path 에 추가, fixture 경로
    ├── fixtures/test_ko.wav     # 합성 22초 3화자 (vibevoice-test 에서 복사)
    ├── test_check_env.sh        # bash 테스트 (Task 1)
    └── test_*.py
```

**테스트 실행 명령**
- 순수 단위 테스트: `cd ~/src/skills && uv run --no-project --with pytest --with numpy pytest skills/asr/tests -m "not slow" -q`
- MLX 통합 테스트(`@pytest.mark.slow`, Task 2 설치 후): `~/.local/share/asr/venv/bin/python -m pytest skills/asr/tests -m slow -q`
  - 처음 한 번 `~/.local/share/asr/venv/bin/pip install pytest` 를 실행한다. 개발용이며 install.sh 에는 넣지 않는다.
- 루트 `pyproject.toml` 은 수정하지 않는다. main 에 사용자의 미커밋 변경이 있기 때문이다.
- `conftest.py` 에 `slow` marker 를 등록한다.

---

### Task 1: 저장소 준비 + `check_env.sh`

**Files:**
- Create: `docs/superpowers/specs/2026-11-10-asr-skill-design.md`, `docs/superpowers/plans/2026-11-10-asr-skill-plan.md`
  - `/Users/yeonjin.roh/src/interview/vibevoice-test/docs/` 의 두 파일을 그대로 복사한다.
- Create: `skills/asr/scripts/check_env.sh`, `skills/asr/tests/test_check_env.sh`, `skills/asr/tests/conftest.py`, `skills/asr/tests/fixtures/test_ko.wav`
  - fixture 원본: `/Users/yeonjin.roh/src/interview/vibevoice-test/test_ko.wav`

**Interfaces:**
- Produces: `check_env.sh` 는 stdout 에 `KEY=VALUE` 한 줄씩 출력하고, 마지막 줄은 `STATUS=ready|missing|unsupported` 다.
  - 종료 코드: ready 0 / missing 10 / unsupported 20
  - 출력 키:
    - 환경: `OS`, `ARCH`, `MEM_GB`, `DISK_FREE_GB`
    - 도구: `BREW=present|absent`, `FFMPEG=present|absent`, `PYTHON=<경로>|absent`
    - 설치: `VENV=present|absent`, `MLX_AUDIO=<버전>|absent`
    - 모델: `MODEL_VIBEVOICE`, `MODEL_VAD`, `MODEL_ECAPA` (각각 `present|missing`)
    - 사유: `UNSUPPORTED_REASON=<문구>` (unsupported 일 때만)
  - 테스트용 덮어쓰기 환경변수: `ASR_UNAME_S`, `ASR_ARM64`(0/1), `ASR_MEM_BYTES`, `ASR_HF_HUB`, `ASR_VENV`, `ASR_PATH_OVERRIDE`
- Rules:
  - unsupported 조건: 다음 중 하나다.
    - OS 가 Darwin 이 아니다.
    - `sysctl -n hw.optional.arm64` 가 1 이 아니다. Rosetta 셸도 잡으려고 `uname -m` 대신 이 값을 본다.
    - 메모리가 24GB 미만이다.
      - 근거: 10분 조각은 실측 기준 약 14GB 이고, OS 몫까지 여유를 둔 값이다.
      - 사유 문구: `메모리 <N>GB — 10분 조각 처리에 최소 24GB가 필요합니다.`
  - `PYTHON` 탐색 순서: `python3.12`, `python3.13`, `python3` 순으로 찾아 3.12 이상인 첫 번째를 쓴다.
  - 모델 `present` 조건: 세 가지를 모두 만족해야 한다.
    - `$HF_HUB/models--<org>--<name>/snapshots/*/` 에 필수 파일이 있다.
      - VibeVoice: `config.json` 과 `*.safetensors` 1개 이상
      - VAD·ECAPA: `model.safetensors`
    - `blobs/` 에 `*.incomplete` 가 없다.
    - 심볼릭 링크가 실제 파일로 해석된다.
  - `MLX_AUDIO` 확인: `$VENV/bin/python -c "import importlib.metadata as m; print(m.version('mlx-audio'))"`

- [ ] **Step 1: 사용자 미커밋 변경 보호 확인.**
  - `git -C ~/src/skills status --short` 결과를 기록한다.
  - 작업은 main 이 아닌 워크트리 브랜치 `feat/asr-skill` 에서 한다. 사용자 변경 파일(`pyproject.toml`, `skills/tech-writer/**`, `uv.lock`)은 건드리지 않는다.
- [ ] **Step 2: 스펙·계획·fixture 를 복사하고 `conftest.py` 를 작성한다.**
  - `conftest.py` 는 `scripts/` 를 sys.path 에 넣고, `FIXTURE = Path(__file__).parent / "fixtures" / "test_ko.wav"` 를 정의하고, `slow` marker 를 등록한다.
- [ ] **Step 3: 실패하는 bash 테스트 `tests/test_check_env.sh` 를 작성한다.**
  - 임시 디렉터리로 가짜 HF hub 와 venv 를 만들고, 덮어쓰기 환경변수로 각 경우를 돌려 출력과 종료 코드를 단언한다.

| 케이스 | 입력 | 기대 |
|---|---|---|
| linux is unsupported | `ASR_UNAME_S=Linux` | `STATUS=unsupported`, exit 20, `UNSUPPORTED_REASON` 포함 |
| intel is unsupported | `ASR_ARM64=0` | exit 20 |
| low memory is unsupported | `ASR_MEM_BYTES=17179869184` | exit 20, 사유에 `16GB` |
| empty hub is missing | 빈 `ASR_HF_HUB` | `MODEL_VIBEVOICE=missing`, exit 10 |
| incomplete model is missing | blobs 에 `x.incomplete` | `MODEL_VAD=missing` |
| all present is ready | 세 모델 + venv(`bin/python` 이 `0.6.5` 를 출력하는 가짜 스크립트) | `STATUS=ready`, exit 0 |

- [ ] **Step 4: 테스트를 실행해 실패를 확인한다.**
  - 명령: `bash skills/asr/tests/test_check_env.sh`
  - 기대: 스크립트가 없어 FAIL
- [ ] **Step 5: `check_env.sh` 를 구현한다.** `set -uo pipefail` 를 쓰고, 덮어쓰기 환경변수가 없으면 실제 시스템 값을 쓴다.
- [ ] **Step 6: 테스트를 실행해 통과를 확인한다.**
  - 명령: `bash skills/asr/tests/test_check_env.sh`
  - 기대: 6 passed
  - 실제 맥 확인: `bash skills/asr/scripts/check_env.sh` 가 `OS=Darwin`, `MEM_GB=48`, `STATUS=missing` 을 출력해야 한다(venv 를 아직 만들지 않았으므로).
- [ ] **Step 7: 커밋한다.**
  - `git add docs/superpowers skills/asr && git commit -m "feat: asr 스킬 설계·계획과 환경 점검 스크립트 추가"`

### Task 2: `install.sh` + `references/install.md`

**Files:**
- Create: `skills/asr/scripts/install.sh`, `skills/asr/references/install.md`

**Interfaces:**
- Consumes: `check_env.sh` 출력 키(Task 1)
- Produces:
  - `install.sh [--dry-run]` 은 빠진 항목만 순서대로 설치한다.
    1. brew ffmpeg / python@3.12
    2. venv
    3. `pip install mlx-audio==0.6.5`
    4. 모델 3종 다운로드: `$VENV/bin/hf download <repo>`, ECAPA 는 `model.safetensors` 만
  - 진행 상황은 `STEP <n>/<total> <설명>` 형식으로 출력한다.
  - 끝나면 `check_env.sh` 를 다시 실행하고, ready 가 아니면 exit 1 이다.
  - `--dry-run` 은 실행하지 않고 할 일 목록과 예상 용량만 출력한다.
  - 예상 용량 상수(빠진 것만 합산):
    - venv 0.5GB
    - VibeVoice 9.5GB
    - VAD 0.002GB
    - ECAPA 0.042GB
    - ffmpeg·python 은 brew 가 보고하는 값을 쓰거나 생략한다.
- Rules:
  - Homebrew 가 없는데 ffmpeg 나 python 이 필요하면, `Homebrew가 없어 자동 설치할 수 없습니다. https://brew.sh 에서 설치한 뒤 다시 실행해 주세요.` 를 출력하고 exit 2 한다.
  - 디스크 여유가 예상 용량 + 2GB 보다 작으면 설치하지 않고 exit 3 한다.

- [ ] **Step 1: `references/install.md` 를 작성한다.** 다음 다섯 가지를 담는다.
  - check_env 결과를 해석하는 표: STATUS 별로 할 일
  - 고지 문안 템플릿: 설치 항목, 예상 용량, 설치 위치, 제공 기능, 소요 시간 안내
    - 소요 시간 예: "실측 약 6MB/s, 9.5GB 기준 수십 분"
  - 동의를 받는 방법과 거절 시 종료 문구
  - `install.sh --dry-run` → 고지 → 동의 → `install.sh` 순서
  - unsupported 와 Homebrew 부재 시 안내 문구(Global Constraints 문구 그대로)
- [ ] **Step 2: `install.sh` 를 구현하고 `--dry-run` 을 검증한다.**
  - 명령: `bash skills/asr/scripts/install.sh --dry-run`
  - 기대: 이 맥에서 venv·모델 3종이 할 일로 나열되고, 예상 합계 약 10GB 가 출력된다.
- [ ] **Step 3: 실제 설치를 실행하고 검증한다.** 사용자가 이미 동의한 개발 환경이다.
  - 명령: `bash skills/asr/scripts/install.sh && bash skills/asr/scripts/check_env.sh`
  - 기대: `STATUS=ready`
  - VibeVoice 모델은 이미 캐시에 있으므로 다운로드는 VAD·ECAPA 만 일어난다.
- [ ] **Step 4: 커밋한다.**
  - `git commit -m "feat: asr 설치 스크립트와 설치 고지 지침 추가"`

### Task 3: `asrlib/audio.py`

**Files:** Create `skills/asr/scripts/asrlib/{__init__,audio}.py`, `skills/asr/tests/test_audio.py`

**Interfaces:**
- Produces:
  - `SR: int = 16000`
  - `ENHANCE_LUFS_THRESHOLD: float = -40.0`
  - `ENHANCE_FILTERS: str = "highpass=f=80,loudnorm=I=-23:LRA=11:TP=-2"`
  - `decode(path: str, filters: str | None = None) -> np.ndarray`
    - 16kHz 모노 float32 를 돌려준다.
    - ffmpeg 를 인자 리스트로 호출한다(셸 문자열이 아님).
    - 디코딩에 실패하면 `AudioError(msg)` 를 던진다.
  - `integrated_lufs(path: str) -> float`
    - ffmpeg `ebur128` 의 `I:` 값을 쓴다.
  - `needs_enhancement(lufs: float) -> bool`
  - `class AudioError(Exception)`

- [ ] **Step 1: 실패하는 테스트를 작성한다.**
  - `test_decode_fixture`: 길이가 22.2초 ±0.1, dtype 이 float32
  - `test_decode_path_with_space`: fixture 를 `tmp_path/"녹음 파일 1.wav"` 로 복사해 디코딩
  - `test_decode_bad_file_raises`: 텍스트 파일을 넣으면 `AudioError`
  - `test_lufs_fixture`: -17.6 ±1.0
  - `test_quiet_needs_enhancement`: ffmpeg `volume=-30dB` 로 만든 사본이 -40 미만이고 `needs_enhancement` 가 True
  - `test_enhance_raises_level`: `decode(quiet, ENHANCE_FILTERS)` 의 RMS 가 `decode(quiet)` RMS 의 5배 이상
  - `test_original_untouched`: 위 동작 전후로 원본 파일의 sha256 이 같다
- [ ] **Step 2: 테스트를 실행해 실패를 확인한다.** 순수 단위 테스트 명령을 쓴다. 기대: ImportError
- [ ] **Step 3: 구현한다.**
- [ ] **Step 4: 테스트를 실행해 통과를 확인한다.** 기대: 7 passed
- [ ] **Step 5: 커밋한다.** `feat: asr 오디오 디코딩·음량 측정·보정 추가`

### Task 4: `asrlib/chunking.py`

**Files:** Create `skills/asr/scripts/asrlib/chunking.py`, `skills/asr/tests/test_chunking.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Chunk: index: int; start: float; end: float`
  - `plan_chunks(duration: float, speech: list[tuple[float, float]], max_len: float = 600.0, overlap: float = 45.0, search: float = 120.0) -> list[Chunk]`
  - `keep_windows(chunks: list[Chunk]) -> list[tuple[float, float]]`
    - 조각마다 최종 전사본에 남길 구간(원본 기준 초)을 돌려준다.
    - 경계는 인접 조각의 겹친 구간 중점이다.
    - 첫 조각은 0부터, 마지막 조각은 `duration` 까지다.
- Algorithm (`plan_chunks`):
  - `duration <= max_len` 이면 `[Chunk(0, 0, duration)]` 을 돌려준다.
  - 그렇지 않으면 `s = 0` 부터 다음을 반복한다.
    1. `limit = s + max_len` 이다.
    2. `limit >= duration` 이면 마지막 조각 `[s, duration]` 을 만들고 끝낸다.
    3. `[limit - search, limit]` 안의 묵음 구간 중 `limit` 에 가장 가까운 것의 중점을 `cut` 으로 삼는다.
       - 묵음 구간은 `speech` 사이의 빈틈 가운데 0.3초 이상인 것이다.
       - 그런 묵음이 없으면 `cut = limit` 이다.
    4. 조각 `[s, cut]` 을 만든다.
    5. 다음 시작은 `s = cut - overlap` 이다.

- [ ] **Step 1: 실패하는 테스트를 작성한다.**
  - `test_short_single_chunk`: 300초면 조각 1개
  - `test_every_chunk_within_10min`: 7200초에 30초마다 1초 묵음이면 모든 조각이 `end - start <= 600` 이고, 마지막 조각의 `end == 7200`
  - `test_cut_at_silence`: 묵음 `(560, 562)` 하나만 있으면 첫 조각 `end == 561.0`
  - `test_no_silence_hard_cut`: 끊김 없는 발화면 첫 조각 `end == 600.0`
  - `test_overlap`: 인접 조각에서 `next.start == prev.end - 45`
  - `test_keep_windows_partition`: 각 window 가 빈틈과 겹침 없이 `[0, duration]` 을 덮고, 각 window 는 자기 조각 범위 안에 있다
- [ ] **Step 2: 테스트를 실행해 실패를 확인한다.**
- [ ] **Step 3: 구현한다.**
- [ ] **Step 4: 테스트를 실행해 통과를 확인한다.** 기대: 6 passed
- [ ] **Step 5: 커밋한다.** `feat: asr 10분 이하 조각 계획 추가`

### Task 5: `asrlib/transcript.py`

**Files:** Create `skills/asr/scripts/asrlib/transcript.py`, `skills/asr/tests/test_transcript.py`

**Interfaces:**
- 문서(dict) 스키마는 스펙 §4-⑦ 의 json 구조를 따른다.
  - 키: `source`, `enhanced`, `chunks`, `speakers`, `links`, `segments`, `failed_ranges`
  - `failed_ranges` 는 이 계획에서 추가한 키다. 형식은 `[{start, end, reason}]` 이다.
- Produces:
  - `resolve(speakers: dict, sid: str) -> str`
    - `merged_into` 를 끝까지 따라간다.
  - `display_name(speakers: dict, sid: str) -> str`
  - `render_markdown(doc: dict) -> str`
    - segments 를 시간순으로 렌더링한다.
    - 표시 이름이 같은 연속 발화는 한 단락으로 합친다. 텍스트는 공백 하나로 잇고, 시각은 첫 발화 시각을 쓴다.
    - `failed_ranges` 는 해당 시각 위치에 `> ⚠ [HH:MM:SS–HH:MM:SS] 이 구간은 인식에 실패했습니다.` 를 끼워 넣는다.
  - `first_lines(doc: dict, n: int = 2) -> dict[str, str]`
    - 키는 표시 이름이다.
    - 값은 그 화자의 첫 발화들에서 앞 n 문장을 뽑은 것이다. 최대 120자다.
  - `output_paths(input_path: str, cwd: str) -> tuple[Path, Path]`
    - `.json` 과 `.md` 경로를 돌려준다.
    - 기본은 입력 파일과 같은 폴더다.
    - 다음 두 경우에는 `cwd` 를 쓴다.
      - 입력이 `~/Library/` 아래에 있다.
      - 입력 폴더에 쓰기 권한이 없다(`os.access(dir, os.W_OK)`).
    - 같은 이름의 결과가 있으면 `<stem> (2)`, `(3)` … 으로 비어 있는 이름을 찾는다. json 과 md 가 같은 번호를 쓴다.
  - `save(doc: dict, json_path: Path, md_path: Path) -> None`
    - UTF-8, `ensure_ascii=False`, indent 2
  - `load(json_path: Path) -> dict`

- [ ] **Step 1: 실패하는 테스트를 작성한다.**
  - `test_render_basic`: 기대 문자열 `**[00:00:01] 화자 1:** 안녕하세요\n\n**[00:00:05] 화자 2:** 네\n`
  - `test_render_uses_name`
  - `test_render_follows_merged_into`: S3 가 `merged_into: S1` 이면 S3 발화가 S1 표시 이름으로 나온다
  - `test_same_display_name_consecutive_merged`: S1, S2 의 name 이 둘 다 "김철수"면 연속 발화가 한 단락이 된다
  - `test_failed_range_marker`
  - `test_first_lines`
  - `test_output_paths_same_dir`
  - `test_output_paths_library_goes_to_cwd`: 입력이 `Path.home()/"Library/Group Containers/x/a.m4a"` 면 cwd 를 쓴다. 문자열 판정만 하므로 파일이 실제로 있을 필요는 없다
  - `test_output_paths_readonly_dir_goes_to_cwd`: chmod 555 폴더
  - `test_output_paths_collision`: `a.md` 가 있으면 `a (2).json` / `a (2).md`
  - `test_save_load_roundtrip_korean`
- [ ] **Step 2: 테스트를 실행해 실패를 확인한다.**
- [ ] **Step 3: 구현한다.**
- [ ] **Step 4: 테스트를 실행해 통과를 확인한다.** 기대: 11 passed
- [ ] **Step 5: 커밋한다.** `feat: asr 전사본 문서 모델과 마크다운 렌더링 추가`

### Task 6: `asrlib/speakers.py`

**Files:** Create `skills/asr/scripts/asrlib/speakers.py`, `skills/asr/tests/test_speakers.py`

**Interfaces:**
- Consumes: `transcript.resolve` (Task 5)
- Produces:
  - `MIN_SEG_SEC = 2.0`, `TRIM_SEC = 0.25`, `MAX_PRINT_SEC = 60.0`, `LINK_THRESHOLD = 0.5`, `MERGE_THRESHOLD = 0.7`, `MIN_OVERLAP_VOTE_SEC = 3.0`
  - `@dataclass class LocalSpeaker: local: int; voiceprint: np.ndarray | None; speech_sec: float; first_start: float`
    - `first_start` 는 원본 기준 초다.
  - `local_speakers(segs: list[dict], audio: np.ndarray, sr: int, offset: float, embed: Callable[[np.ndarray], np.ndarray]) -> dict[int, LocalSpeaker]`
    - `segs` 는 조각 기준 시각이고, 각 항목은 `{start, end, speaker_id: int, text}` 다.
    - 지문은 다음 순서로 만든다.
      1. `MIN_SEG_SEC` 이상인 구간만 쓴다.
      2. 양끝을 `TRIM_SEC` 씩 깎는다.
      3. 긴 구간부터 누적 `MAX_PRINT_SEC` 까지 고른다.
      4. 고른 구간마다 `embed` 를 호출한다.
      5. `speech_sec` 가중 평균을 낸 뒤 L2 정규화한다.
    - 쓸 구간이 없으면 `voiceprint=None` 이다.
  - `overlap_votes(prev: list[dict], nxt: list[dict], window: tuple[float, float]) -> dict[int, str]`
    - `prev` 는 원본 기준 시각이고, 전역 `speaker` 를 가진 이미 연결된 segments 다.
    - `nxt` 는 원본 기준 시각이고, `speaker_id` 는 아직 로컬 번호다.
    - `window` 안에서 (로컬, 전역) 쌍마다 시간 겹침(초)을 합산한다.
    - 서로 최선인 쌍 가운데 합이 `MIN_OVERLAP_VOTE_SEC` 이상인 것만 돌려준다.
  - `link_chunk(speakers: dict, links: list, chunk_index: int, locals_: dict[int, LocalSpeaker], votes: dict[int, str]) -> dict[int, str]`
    - 로컬 번호를 전역 ID 에 매핑해 돌려준다.
    - `speakers` 와 `links` 를 제자리에서 갱신한다.
    - 연결 순서:
      1. votes 로 연결한다(`how: "overlap"`). 지문이 있으면 유사도도 기록한다.
      2. 나머지는 코사인 유사도 행렬을 만들어 높은 순으로 탐욕적 일대일 배정을 한다. 이미 쓴 전역 ID 는 제외한다. `LINK_THRESHOLD` 이상이면 `how: "voice"` 다.
      3. 그래도 남은 화자는 새 ID 로 등록한다(`how: "new"` 또는 `"new-noprint"`).
    - 첫 조각은 모두 `how: "seed"` 다.
    - 새 ID 는 `S{n}` 이다. n 은 지금까지 쓴 최대 번호 + 1 이다.
    - 연결되면 명부 지문을 `speech_sec` 가중 평균으로 갱신하고 다시 정규화한다.
  - `consolidate(speakers: dict) -> list[tuple[str, str, float]]`
    - merged 가 아니고 지문이 있는 화자 쌍 가운데 유사도가 가장 높은 쌍이 `MERGE_THRESHOLD` 이상이면 합친다.
    - 합칠 때는 `first_seen` 이 늦은 쪽을 이른 쪽에 합치고(`merged_into`, `merge_similarity`), 지문을 가중 평균으로 갱신한다.
    - 합칠 쌍이 없을 때까지 반복한다.
    - 합친 목록 `(from, into, sim)` 을 돌려준다.
  - `assign_labels(speakers: dict) -> None`
    - merged 가 아닌 화자에게 `first_seen` 순서로 `label_no` 1..N 을 매긴다.
- JSON 저장 시 `voiceprint` 는 float 리스트로 넣는다. 소수 6자리로 반올림한다.

- [ ] **Step 1: 실패하는 테스트를 작성한다.** 지문은 직교 단위벡터에 작은 잡음을 섞어 합성한다.
  - `test_local_speakers_skips_short`: 1.5초 구간만 있는 화자는 `voiceprint is None`
  - `test_local_speakers_trims_and_caps`: 가짜 embed 가 받은 길이 합이 60초 × SR 이하이고, 각 호출 길이가 `(dur - 0.5) × SR`
  - `test_seed_first_chunk`: 로컬 0, 1 이 S1, S2 가 되고 links 가 seed
  - `test_link_by_voice_swapped_ids`: 조각 2 의 로컬 0 이 S2 지문, 로컬 1 이 S1 지문이면 매핑이 `{0: "S2", 1: "S1"}`
  - `test_new_speaker_joins_midway`: 조각 2 에 직교 지문의 로컬 2 가 있으면 S3 이 되고 `first_seen` 이 그 시각
  - `test_link_speaker_without_print`: 지문 None 이고 votes 도 없으면 새 ID, `how == "new-noprint"`, 예외 없음
  - `test_overlap_votes_mutual_best`
  - `test_overlap_vote_below_min_ignored`: 2초 겹침은 무시
  - `test_one_to_one`: 두 로컬이 모두 S1 과 가장 비슷해도 S1 은 하나에만 배정
  - `test_consolidate_merges_split_person`: S1 과 S3 의 지문이 cos 0.9 면 S3 이 `merged_into` S1, 반환값 1개
  - `test_consolidate_keeps_distinct`: cos 0.6 이면 합치지 않음
  - `test_assign_labels_skip_merged`: S1, S3(merged), S4 면 label_no 가 S1=1, S4=2
- [ ] **Step 2: 테스트를 실행해 실패를 확인한다.**
- [ ] **Step 3: 구현한다.**
- [ ] **Step 4: 테스트를 실행해 통과를 확인한다.** 기대: 12 passed
- [ ] **Step 5: 커밋한다.** `feat: asr 목소리 지문 기반 조각 간 화자 연결과 통합 추가`

### Task 7: `asrlib/engine.py` (mlx-audio 래퍼)

**Files:** Create `skills/asr/scripts/asrlib/engine.py`, `skills/asr/tests/test_engine_slow.py`

**Interfaces:**
- Produces:
  - `class TranscriptionFailed(Exception)`
  - `class Engine`
    - `__init__(self, asr_repo="mlx-community/VibeVoice-ASR-8bit", vad_repo="mlx-community/silero-vad", ecapa_repo="aufklarer/SpeechBrain-ECAPA-VoxCeleb-20M-MLX")`
      - 모델은 처음 쓸 때 불러온다. mlx import 는 메서드 안에서만 한다.
    - `speech_intervals(self, audio: np.ndarray) -> list[tuple[float, float]]`
      - `mlx_audio.vad.load(vad_repo).get_speech_timestamps(audio, sample_rate=16000, return_seconds=True)` 를 쓴다.
    - `transcribe(self, audio: np.ndarray) -> list[dict]`
      - `mlx_audio.stt.load(asr_repo).generate(audio, sampling_rate=16000, max_tokens=16384)` 를 쓴다.
      - `.segments` 의 각 항목을 `{start, end, speaker_id, text}` 로 정규화한다(조각 기준 시각).
      - `speaker_id is None` 인 항목은 뺀다.
      - 실패 판정: 다음 중 하나면 실패다.
        - segments 가 비었는데 `.text` 에 대괄호 태그 외 글자가 있다(파싱 실패).
        - 마지막 `end` 가 `len(audio)/SR - 60` 보다 작고, VAD 상 그 뒤에 발화가 있다(끊김).
      - 실패하면 `repetition_penalty=1.1` 로 한 번 더 시도하고, 그래도 실패면 `TranscriptionFailed(reason)` 를 던진다.
    - `embed(self, audio: np.ndarray) -> np.ndarray`
      - `EcapaSpeakerEmbeddingExtractor.from_pretrained(ecapa_repo).encode(audio)` → `np.ndarray(192,)`

- [ ] **Step 1: slow 테스트를 작성한다.**
  - `test_vad_on_fixture`: 구간 1개 이상, 처음 시작 < 0.5초, 마지막 끝 > 21초
  - `test_transcribe_fixture`: segments ≥ 4, 모든 `speaker_id` 가 int, 전체 텍스트에 "물류센터" 포함
  - `test_embed_unit_norm`: 길이 192, 노름 1 ±1e-3
- [ ] **Step 2: 테스트를 실행해 실패를 확인한다.** MLX 통합 테스트 명령을 쓴다.
- [ ] **Step 3: 구현한다.**
- [ ] **Step 4: 테스트를 실행해 통과를 확인한다.** 기대: 3 passed. 소요 시간 1~2분
- [ ] **Step 5: 커밋한다.** `feat: asr mlx-audio 엔진 래퍼 추가`

### Task 8: `transcribe.py` CLI (오케스트레이션)

**Files:** Create `skills/asr/scripts/transcribe.py`, `skills/asr/tests/test_transcribe.py`

**Interfaces:**
- Consumes: Task 3~7 의 전부
- Produces:
  - `run(input_path: str, cwd: str, engine) -> dict`
    - 테스트를 위해 엔진을 주입받는다.
    - 반환하는 요약 dict 의 키:
      - `json`, `md`: 경로
      - `speakers`: `[{label, first_lines}]`
      - `enhanced`: `{lufs_before} | None`
      - `merged`: `[{from_label, into_label, similarity}]`
      - `failed_ranges`
      - `no_speech: bool`
  - CLI: `python transcribe.py <audio> [--cwd DIR]`
    - 요약을 JSON 한 덩어리로 stdout 에 출력한다.
    - 진행 상황은 stderr 에 `조각 2/7 처리 중 (00:09:15–00:19:15)` 형식으로 낸다.
    - 종료 코드는 성공 0, 입력 오류(`AudioError`) 2 다.
- 흐름:
  1. LUFS 를 측정한다.
  2. 필요하면 `decode(path, ENHANCE_FILTERS)`, 아니면 `decode(path)` 로 디코딩한다.
  3. `speech_intervals` 를 구한다.
  4. `plan_chunks` 로 조각을 나눈다.
  5. 조각마다 다음을 한다.
     - `transcribe` 를 호출한다. `TranscriptionFailed` 면 `failed_ranges` 에 추가하고 다음 조각으로 넘어간다.
     - 원본 기준 시각으로 바꾼다.
     - `local_speakers` 로 지문을 만든다.
     - `overlap_votes(prev, nxt, window)` 를 구한다. window 는 앞 조각과 이 조각이 겹친 구간 `[chunk.start, prev_chunk.end]` 이다.
     - `link_chunk` 로 연결한다.
  6. 모든 segments 를 모은 뒤 `keep_windows` 로 겹침 중복을 뺀다. 판정 기준은 segment 시작 시각이다.
  7. `consolidate` → `assign_labels` 를 한다.
  8. `first_seen` 과 `speech_sec` 를 기록한다.
  9. `save` 하고 요약을 돌려준다.

- [ ] **Step 1: 실패하는 테스트를 작성한다.** `FakeEngine` 이 조각 구간에 따라 미리 정한 segments 와 지문을 돌려준다.
  - `test_short_file_single_chunk`: fixture. FakeEngine 이 2화자를 돌려주면 `.md` 생성, `speakers` 2개, 원본 sha256 불변
  - `test_long_file_links_across_chunks`: duration 1500초 가짜 오디오(zeros). 3개 조각에서 로컬 번호가 뒤섞여도 최종 전역 화자가 2명이고, 조각 2 에 처음 나온 화자가 `화자 3`
  - `test_overlap_not_duplicated`: 겹친 구간 발화가 `.md` 에 한 번만 나온다
  - `test_failed_chunk_marked`: 조각 2 에서 `TranscriptionFailed` 면 `failed_ranges` 1개, `.md` 에 ⚠ 줄, 나머지 조각은 처리됨
  - `test_all_silence`: 모든 조각이 빈 segments 면 `no_speech=True`, 예외 없음
  - `test_quiet_file_enhanced`: `volume=-30dB` 사본이면 `enhanced.lufs_before < -40`
- [ ] **Step 2: 테스트를 실행해 실패를 확인한다.**
- [ ] **Step 3: 구현한다.**
- [ ] **Step 4: 테스트를 실행해 통과를 확인한다.** 기대: 6 passed
- [ ] **Step 5: 실제 엔진으로 e2e 를 확인한다.**
  - 명령: `~/.local/share/asr/venv/bin/python skills/asr/scripts/transcribe.py skills/asr/tests/fixtures/test_ko.wav --cwd /tmp/asr-e2e`
  - 기대: exit 0, 요약 JSON 의 `md` 파일이 있고 화자가 2명 이상
- [ ] **Step 6: 커밋한다.** `feat: asr 전사 CLI 추가`

### Task 9: `rename.py` CLI

**Files:** Create `skills/asr/scripts/rename.py`, `skills/asr/tests/test_rename.py`

**Interfaces:**
- Consumes: `transcript.load/save/render_markdown/display_name` (Task 5)
- Produces:
  - `rename(json_path: Path, mapping: dict[int, str]) -> dict`
    - `mapping` 의 키는 `label_no` 다.
    - `.json` 의 `name` 을 갱신하고, 같은 이름의 `.md` 를 다시 만든다.
    - 알 수 없는 번호는 `ValueError("화자 N 이 없습니다")` 다.
    - 반환값: `{md, speakers: [{label_no, name}]}`
  - CLI: `python rename.py <json> --set "1=김철수" --set "3=박지민"`

- [ ] **Step 1: 실패하는 테스트를 작성한다.**
  - `test_rename_updates_md`
  - `test_partial_rename_keeps_default`
  - `test_same_name_merges_paragraphs`: 1 과 2 에 같은 이름을 주면 연속 발화가 한 단락
  - `test_unknown_label_errors`
  - `test_rename_does_not_touch_segments`: 이름 바꾸기 전후로 segments 가 같다
- [ ] **Step 2: 테스트를 실행해 실패를 확인한다.**
- [ ] **Step 3: 구현한다.**
- [ ] **Step 4: 테스트를 실행해 통과를 확인한다.** 기대: 5 passed
- [ ] **Step 5: 커밋한다.** `feat: asr 화자 이름 바꾸기 CLI 추가`

### Task 10: `SKILL.md` + 설치 링크 + 실녹음 검증

**Files:** Create `skills/asr/SKILL.md`. Modify `README.md`: asr 설치·사용 섹션을 추가하고, 기존 내용은 건드리지 않는다.

**SKILL.md 요구사항**
- frontmatter 는 `name: asr` 이다.
- description 은 한국어로, 트리거를 적극적으로 적는다.
  - 녹음·음성 파일 전사, 회의록·면접 녹취, 화자분리, "누가 뭐라고 했는지", m4a·음성 메모 언급 시
  - 요약·평가 요청은 다른 스킬 몫이라고 명시한다.
- 본문은 다음 순서다.
  1. `check_env.sh` 를 실행한다.
     - unsupported 면 안내하고 멈춘다.
     - missing 이면 `references/install.md` 를 따른다.
  2. 전사를 실행한다.
     - 명령: `~/.local/share/asr/venv/bin/python <skill>/scripts/transcribe.py "<경로>" --cwd "$PWD"`
     - 길면 Bash `run_in_background` 로 돌린다. 예상 시간 안내: 실측 12분 녹음에 약 3분.
  3. 결과를 안내한다. 요약 JSON 으로 아래 문안을 채운다.
  4. 사용자가 이름을 주면 `rename.py` 를 실행한다.
- 결과 안내 문안(스펙 §4-⑧)

  ```
  전사를 마쳤습니다: <md 경로>
  [보정 시] 녹음 음량이 매우 낮아(<lufs> LUFS) 보정본으로 분석했습니다. 원본은 그대로입니다.
  [통합 시] 목소리가 같은 화자 <n>쌍을 하나로 합쳤습니다.
  [실패 구간 시] 다음 구간은 인식에 실패했습니다: <구간들>
  - 화자 1: "<첫 발언>"
  - 화자 2: "<첫 발언>"
  화자는 화자1, 화자2입니다. 이름을 바꾸려면 원하는 이름을 알려 주세요.
  ```

- [ ] **Step 1: SKILL.md 를 작성하고 README 섹션을 추가한다.**
- [ ] **Step 2: 설치 링크를 만든다.** `ln -s ~/src/skills/skills/asr ~/.claude/skills/asr`
  - 이미 있으면 대상을 확인만 하고 덮어쓰지 않는다.
- [ ] **Step 3: 12분 실녹음으로 검증한다.**
  - 입력은 사용자 녹음 복제본이다. 외부 반출 금지.
  - 명령: `transcribe.py /Users/yeonjin.roh/src/interview/vibevoice-test/memo12.m4a --cwd /tmp/asr-real`
  - 기대: 화자 2명, `failed_ranges` 비어 있음, 피크 메모리 16GB 이하(`/usr/bin/time -l`)
- [ ] **Step 4: 긴 녹음 검증**
  - 입력: 51분 녹음 복제본
    - `cp ~/"Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/20260804 104510.m4a" /tmp/asr-real/long.m4a`
  - 기대: 조각 6개 이상, 모든 조각 ≤600초(json `chunks`), 원본 sha256 불변, 피크 메모리 16GB 이하
  - 화자 수와 연결 품질은 사용자가 `.md` 를 보고 판단한다.
- [ ] **Step 5: 전체 단위 테스트를 다시 돌리고 커밋한다.** `docs: asr 스킬 SKILL.md 와 사용 안내 추가`
