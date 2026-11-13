# Tech Writing

독자에 맞춰 한국어 기술 문서를 구성·작성·개선하는 Claude Code 플러그인입니다. 독자 config로 문서 수준을 정하고, 문서 유형에 맞는 구조로 쓰고, mermaid·diagram-design으로 시각화한 뒤, korean-proofreader 교정과 독자 되말하기 검증까지 한 흐름으로 처리합니다.

## 설치

```
/plugin marketplace add ~/src/skills
/plugin install tech-writing@tech-writing
```

## 사용

설치하면 기술 문서를 쓰거나 고치는 요청에 자동으로 발동합니다. 직접 부를 수도 있습니다.

```
/tech-writing:tech-writer
```

요청 예시:

- "이 저장소 README 만들어줘"
- "어제 장애 메모로 포스트모템 작성해줘"
- "이 설계 문서 구조 개선해줘. PM도 읽을 수 있게"
- "기본 독자를 신규 입사 백엔드 개발자로 바꿔줘"

처음 실행하면 독자 config(`~/.claude/tech-writing/config.yaml`)를 만드는 짧은 질문을 합니다. 프로젝트마다 독자가 다르면 저장소 루트에 `.tech-writing.yaml`을 두어 덮어씁니다.

## 함께 쓰는 도구

| 도구 | 용도 | 없을 때 |
|---|---|---|
| [humanizer-ko](https://github.com/nyjin/humanizer-ko) `korean-proofreader` | 문단·문장 교정 | 기술 문서 문장 규칙만 적용 |
| `eli5` 스킬 | 비개발 독자용 개념 설명 | 내장 수준별 전략으로 직접 설명 |
| `diagram-design` 스킬 | 복잡한 다이어그램(HTML → SVG/PNG 내보내기) | mermaid만 사용 |
| [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) | 다이어그램 이미지 렌더링 | `npx`로 자동 실행 |
| [uv](https://docs.astral.sh/uv/) | 스크립트 실행(config 병합, 문서 검사) | 필수 |

## harness-loop

같은 marketplace의 두 번째 플러그인입니다. Claude Code 세션에서 하네스 결함 신호(도구 재시도, 사용자 교정, 불필요한 확인 등)를 모아 점수를 매기고, 반복되는 문제를 스킬·규칙·스크립트 개선안(action item)으로 제안합니다. tech-writing과 따로 켜고 끕니다.

### 설치

```
/plugin marketplace add ~/src/skills
/plugin install harness-loop@tech-writing
```

### 사용

설치하면 세션이 끝날 때 신호를 모아 백그라운드에서 판정하고, 다음 세션을 시작할 때 검토 대기 항목을 알려 줍니다. 직접 부를 때는 아래 명령을 씁니다.

| 명령 | 하는 일 |
|---|---|
| `/harness-loop:retro` | 현재 세션을 회고해 action item 리포트를 만든다 |
| `/harness-loop:retro review` | 다른 세션에서 쌓인 검토 대기 항목을 보여 준다 |
| `/harness-loop:retro backfill [--days N]` | 설치 전 기록을 판정한다(하루 판정 상한 적용) |

리포트를 받은 뒤에는 "1 승인, 2 거절", "2번 영향 3으로"처럼 자유롭게 답하면 결정을 기록하고, 승인한 항목은 대상 파일의 소유 환경(저장소, 로컬 파일, 플러그인 캐시)에 맞게 반영합니다. 채점 기준(`scoring.yaml`, `rubric.md`)과 결정 기록은 플러그인 데이터 디렉터리에 있습니다.

## 개발

```bash
uv sync
uv run pytest            # 전체 테스트
uv run pytest -m "not slow"   # mermaid 렌더링 제외
```

## 출처

이 플러그인의 원칙은 아래 자료를 종합해 새로 썼습니다. 원문 문장과 예시는 옮기지 않았습니다. 종합 과정은 `docs/research/synthesis.md`에 있습니다.

| 자료 | 라이선스·고지 |
|---|---|
| [Toss 테크니컬 라이팅 가이드](https://github.com/toss/technical-writing) | CC BY-NC-SA 4.0 — 원칙만 참고 |
| [Easy-Vibe 기술 문서 작성 입문](https://datawhalechina.github.io/easy-vibe/ko-kr/appendix/9-engineering-excellence/technical-writing.html) | CC BY-NC-SA 4.0 — 원칙만 참고 |
| [인포그랩 테크니컬 라이팅 10가지 원칙](https://insight.infograb.net/blog/2023/03/30/technical-writing-guide/) | 2차 가공 금지 고지 — 원칙만 참고 |
| [테크니컬 라이팅이란? (IT인을 위한 글쓰기 가이드)](https://it-ist.tistory.com/274) | |
| [글쓰기가 서툰 기술자를 위한 테크니컬 라이팅 10계명](https://brunch.co.kr/@yooncohg/133) | |
| [Technical Writing for Engineers](https://www.linkedin.com/pulse/technical-writing-engineers-agoti-dennis/) | |
| [How to Create Technical Documentation with Examples](https://www.linkedin.com/pulse/how-create-technical-documentation-examples-basit-saleem-ckkqe/) | |
| [개발자의 글쓰기: Technical Writing #1](https://devocean.sk.com/blog/techBoardDetail.do?ID=165343) | |
| [테크니컬 라이팅 가이드: 개발자를 위한 글쓰기 기본기](https://velog.io/@nunomi0/테크니컬-라이팅-가이드-개발자를-위한-글쓰기-기본기) | |
| [Docs for Developers 리뷰](https://shinjungoh.tistory.com/entry/Docs-for-Developers-기술-문서-작성-완벽-가이드-리뷰) | |
| [테크니컬 라이팅 문서 작성 가이드](https://velog.io/@ailab/테크니컬-라이팅-문서-작성-가이드) | |

본보기(`references/exemplars/`)를 쓸 때 분석한 한국어 참고 문서 목록은 `skills/tech-writer/references/exemplar-notes.md`에 있습니다. 본보기는 분석한 패턴을 바탕으로 새로 썼습니다.

## asr: 화자별 녹음 전사

`skills/asr/` 는 녹음 파일을 화자별로 나눈 한국어 전사본(마크다운)으로 만드는 Claude Code 스킬입니다. 회의·면접 녹음을 받아 화자분리와 타임스탬프를 붙이고, 원하면 화자에 실제 이름을 붙입니다. VibeVoice-ASR 을 MLX 로 이 Mac 안에서만 돌리며, 녹음과 전사본은 외부로 나가지 않습니다. 요약과 평가는 하지 않습니다.

**지원 환경:** Apple Silicon(M1 이상) Mac. 메모리는 24GB 이상을 권장합니다(실측 피크 약 17.6GB). 더 작아도 실행은 하지만 느려지거나 메모리 부족으로 멈출 수 있습니다.

### 설치

플러그인이 아니라 스킬 폴더 링크로 설치합니다. tech-writing 플러그인에는 들어 있지 않습니다(`marketplace.json` 의 `skills` 가 `skills/tech-writer` 만 싣습니다).

```bash
ln -s ~/src/skills/skills/asr ~/.claude/skills/asr
```

처음 실행하면 스킬이 환경을 점검하고, 빠진 구성 요소(ffmpeg, Python 3.12, 가상환경 `~/.local/share/asr/venv`, 모델 약 10GB)를 알린 뒤 동의를 받아 설치합니다. 자세한 절차는 `skills/asr/references/install.md` 에 있습니다.

### 사용

녹음 파일을 주며 전사를 요청하면 발동합니다.

- "~/Downloads/회의.m4a 전사해 줘"
- "이 면접 녹음 누가 뭐라고 했는지 정리해 줘"
- "화자1은 김철수, 화자2는 박지민으로 바꿔 줘"

전사본은 녹음과 같은 폴더에 `<녹음 이름>.md`, `<녹음 이름>.json` 으로 저장합니다. 그 폴더에 쓸 수 없거나 음성 메모 보관함이면 현재 폴더에 저장합니다.

### 테스트

```bash
uv run --no-project --with pytest --with numpy pytest skills/asr/tests -m "not slow" -q   # 단위 테스트
bash skills/asr/tests/test_check_env.sh                                                   # 환경 점검 스크립트 테스트
```

MLX 통합 테스트는 설치를 마친 뒤 스킬 전용 가상환경에 pytest 를 한 번 넣고 돌립니다. 모델을 실제로 올리므로 메모리를 많이 씁니다. 한 번에 하나만 실행합니다.

```bash
~/.local/share/asr/venv/bin/pip install pytest                                            # 처음 한 번
~/.local/share/asr/venv/bin/python -m pytest skills/asr/tests -m slow -q                  # MLX 통합 테스트
```

## 라이선스

MIT
