# tech-writer 스킬 설계

- 작성일: 2026-10-15
- 상태: 설계 승인 완료, spec 검토 대기
- 근거 자료: `docs/research/synthesis.md`(레퍼런스 11개 종합·검증본), `docs/research/dodont-catalog.md`(Don't/Do 163쌍, 로컬 전용), `docs/research/exemplars-*.md`(한국어 본보기 후보)

## 1. 목적

독자에 맞춰 기술 문서를 구성하고 작성하는 Claude Code 스킬을 만든다. 독자 수준을 파악하고, 문서 유형에 맞는 구조로 흐름을 잡고, 의도를 간결하게 전달하며, 포매팅과 다이어그램으로 가독성을 높인다.

### 성공 기준

1. 같은 재료로 썼을 때 스킬을 쓴 결과물이 쓰지 않은 결과물보다 낫다. 평가 시나리오(§10)에서 체크리스트 통과율과 독자 되말하기 일치도로 비교한다.
2. 독자 config를 한 번 만들면 이후 문서에서 다시 묻지 않는다.
3. 문서 형식(Markdown, Confluence, HTML)에 맞는 다이어그램이 실제로 렌더링된다.
4. 문서에 확인되지 않은 명령어, 경로, API가 들어가지 않는다. 확인할 수 없는 부분은 `TODO(확인 필요)`로 표시한다.

### 범위 밖

- 비즈니스 문서(사업계획서, 백서, 보도자료)
- 영어 문서 작성
- 문서 사이트 빌드·배포(Docusaurus 등)
- 일반 한국어 문장 교정 규칙의 재구현. 이 부분은 korean-proofreader에 위임한다.

## 2. 배포 형태

humanizer-ko와 같은 플러그인 구조로 만든다. 저장소는 `~/src/skills`다. 로컬 마켓플레이스로 유저 범위에 설치한다.

- 플러그인 이름: `tech-writing`
- 스킬 이름: `tech-writer`

```
~/src/skills/
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── skills/tech-writer/
│   ├── SKILL.md
│   ├── references/
│   │   ├── audience.md
│   │   ├── doc-types.md
│   │   ├── templates/          # 유형별 빈 골격 8개
│   │   ├── structure.md
│   │   ├── style.md
│   │   ├── formatting.md
│   │   ├── visualization.md
│   │   ├── examples.md
│   │   ├── exemplars/          # 유형별 모범 문서 9편
│   │   ├── exemplar-notes.md
│   │   └── review-checklist.md
│   ├── config/default.yaml
│   └── scripts/render-mermaid.sh
├── docs/superpowers/specs/
└── README.md                   # 사용법, 출처, 라이선스 표기
```

## 3. 모드

| 모드 | 진입 신호 | 시작 단계 |
|---|---|---|
| 작성 | "문서 써줘", "README 만들어줘", "이 코드 설계 문서로 정리해줘", "장애 내용 포스트모템으로" | 파이프라인 전체 |
| 개선 | 기존 문서를 주며 "개선해줘", "구조 봐줘", "읽기 어렵다" | 독자 판단부터. 구조 변경은 제안하고 동의를 받은 뒤 고친다 |
| 설정 | "독자 설정", "기본 독자를 PM으로", "용어집 추가" | config 편집 |

트리거 경계: 문장만 다듬는 요청("이 문단 교정해줘")은 korean-proofreader가 맡는다. 이 스킬은 문서 구성과 작성이 필요한 요청에만 발동한다.

## 4. 작성 파이프라인

```mermaid
flowchart LR
  A[config 로드] --> B[독자·목적 결정]
  B --> C[유형 결정·템플릿]
  C --> D[목차]
  D --> E[초안]
  E --> F[시각화]
  F --> G[기술 문서 규칙 점검]
  G --> H[korean-proofreader]
  H --> I[자체 검증]
  I --> J[출력]
```

1. **config 로드.** 유저 config에 프로젝트 config를 키 단위로 덮어쓴다. config가 없으면 3~4문항 인터뷰로 만든 뒤 이어서 진행한다.
2. **독자·목적 결정.** 우선순위는 요청에 명시된 독자 → config `default` 순이다. 재료와 독자가 명백히 어긋날 때만 확인을 요청한다. 독자는 config에 있는데 목적이 불분명하면 1~3개만 묻는다.
3. **유형 결정.** `doc-types.md`의 결정 트리로 고르고 해당 템플릿과 본보기 한 편을 읽는다.
4. **목차.** 근거가 충분하면 목차를 보여 주고 바로 진행한다. 근거가 약하면 승인을 받는다.
5. **초안.** `structure.md`와 `style.md`를 적용한다. 독자 수준과 재료 난이도의 차이가 크면 개념 설명 단계에서 **eli5**를 호출한다. 이때 config의 독자 역할과 수준을 넘긴다.
6. **시각화.** `visualization.md`의 규칙을 따른다(§7).
7. **기술 문서 규칙 점검.** `style.md`와 `formatting.md`의 기계 검사 항목을 확인한다.
8. **korean-proofreader 호출.** 문단과 문장 층위만 맡긴다. 다음 제약을 함께 넘긴다.
   - 용어집, 외래어 표기, 코드·명령어·UI 문자열·고유명사는 바꾸지 않는다.
   - 구조와 섹션 순서는 바꾸지 않는다.
   - config의 어조를 유지한다.
9. **자체 검증.** §8을 따른다.
10. **출력.** 형식은 config의 `output.format`을 따른다. 끝에 독자, 유형, 검증 결과를 짧게 보고한다. 이 보고는 문서 본문에 넣지 않는다.

## 5. config

- 유저: `~/.claude/tech-writing/config.yaml`
- 프로젝트: `<repo>/.tech-writing.yaml` (있으면 유저 설정을 키 단위로 덮어씀)

```yaml
audiences:
  default: backend-dev
  profiles:
    backend-dev:
      role: 백엔드 개발자
      level: intermediate          # beginner | intermediate | expert | non-technical
      knows: [Spring, JPA]         # 설명 없이 써도 되는 것
      needs: [설계 의도, 운영 방법]  # 문서가 먼저 답할 것
      context: 장애 대응 중에 읽음   # 선택
style:
  tone: 합니다체                    # 합니다체 | 해요체 | 개조식
  loanword_policy: usage           # usage | standard
  glossary: {}                     # 표기 → 통일 표기
output:
  format: markdown                 # markdown | confluence | html
  diagrams: auto                   # auto | mermaid | archify | none
  assets_dir: docs/assets
  confluence_mermaid_macro: false
```

- 설정 모드에서 수정할 때는 유저 파일과 프로젝트 파일 중 어디를 고칠지 먼저 묻는다.
- 용어집은 다른 모든 표기 규칙보다 우선한다.

## 6. 지침 내용 (references)

종합본(synthesis v1)의 원칙 ID를 그대로 쓴다. 원칙은 우리 말로 다시 쓰고 출처 ID를 단다.

| 파일 | 담는 원칙 | 핵심 |
|---|---|---|
| `audience.md` | C | config 해석, 수준별 전략, 상황별 필요(절차는 무엇·어떻게, 교육은 왜) |
| `doc-types.md` | D, T | 4유형 결정 트리, 실무 문서 매핑, 유형 결합, 여러 페이지 구조, 매체 선택 |
| `templates/` | D, S | 튜토리얼, how-to, 트러블슈팅, 참조, 설명, 설계·ADR, 런북, 포스트모템 |
| `structure.md` | E, S | 개요 1~2문장, 가치 먼저, 한 문서 한 주제(H4면 분리 제안), 제목(30자 권장, 평서문), 예측 가능성, 설명→코드, 왜와 결정 맥락 |
| `style.md` | F, G, H, I, J, P | 단정적 어조, 수치·기준, 누가·무엇을·어디에, 한 문장 한 생각, 메타 담화 제거, 한 개념 한 용어, 약어 풀어쓰기, 독자 주체 능동형, 경고 강조 |
| `formatting.md` | L, T | 번호·글머리 목록, 한 단계 한 동작, 표, 코드 블록 언어 지정, 경고 패널, 캡션, 형식별 차이(Markdown, Confluence, HTML) |
| `visualization.md` | M | §7 |
| `review-checklist.md` | O, Q | §8, 유지보수 메모(SSOT, deprecated 표시) |

출처 간 충돌은 종합본 §R의 해소안을 따른다. 사용자가 확정한 항목은 다음과 같다.

- **외래어**: 사용 빈도 우선(`usage`). 판단이 어려우면 표준 표기를 쓴다. 용어집이 항상 우선한다.
- **어조**: 한 문서 안에서 섞지 않는다.
- **문장 길이**: 절차는 단문으로 쓴다. 설명 문단은 길이에 변화를 준다.

## 7. 시각화

### 내용 → 다이어그램

| 내용 | 다이어그램 |
|---|---|
| 컴포넌트, 경계, 배포 | flowchart + subgraph (archify `architecture`) |
| 시간 순서 호출 | sequenceDiagram |
| 처리 단계, 분기 | flowchart |
| 상태 전이 | stateDiagram |
| 데이터 모델 | erDiagram / classDiagram |
| 장애 타임라인 | 표 (필요하면 timeline) |
| 비교, 속성 | 표 |

### 형식 × 복잡도 → 도구

복잡의 기준: 주요 노드가 12개를 넘거나, 경계·브랜드 아이콘이 필요한 경우.

| 출력 | 단순 | 복잡 |
|---|---|---|
| Markdown | mermaid 코드 블록 | mermaid 개요도 + archify HTML 링크 |
| Confluence | mmdc로 렌더링한 SVG/PNG 첨부 (매크로가 있으면 코드) | mmdc 개요도 + archify HTML |
| HTML | mmdc SVG 인라인 | archify |

- archify는 CLI로 이미지를 내보내지 못한다. 정적 이미지는 mmdc로만 만든다.
- `render-mermaid.sh`는 `mmdc`를 먼저 쓰고, 없으면 `npx -y @mermaid-js/mermaid-cli`로 실행한다. 렌더링이 실패하면 문법을 고쳐 다시 시도한다.
- 다이어그램 앞에는 무엇을 보여 주는지 한 문장을, 뒤에는 핵심 한 문장을 쓴다. 다이어그램 내용을 글로 다시 늘어놓지 않는다.

## 8. 자체 검증

| 단계 | 내용 |
|---|---|
| 기계 검사 | 제목 30자, H4 깊이, 코드 블록 언어, 약어 첫 등장 풀어쓰기, 용어집 위반, 다이어그램 렌더링 성공 |
| 사실 검증 | 명령어, 경로, API, 설정 키를 재료와 대조. 확인되지 않으면 `TODO(확인 필요)` |
| 되말하기 | 서브에이전트에게 config의 독자 역할을 주고 3줄 요약과 막힌 곳을 받는다. 의도와 어긋나면 고친다 |
| 체크리스트 | 유형별 필수 요소, 구조, 문장 |

## 9. 예시와 본보기

### `examples.md`

- 원칙 ID별로 3~5쌍을 둔다. 각 쌍은 ❌ Don't, ✅ Do, 왜(한 줄)로 쓴다.
- 패턴은 카탈로그에서 가져오고 문장은 새로 쓴다. 소재는 백엔드, 운영, 배포 장면이다.
- 시각화, 포매팅, 코드 예제는 출처에 예시가 부족하므로 직접 만든다.
- 일반 문장 교정 패턴은 넣지 않는다. 명사화만 남긴다.

### `exemplars/`

- 9편: 유형 8개 + 비개발 독자용 설명 1편.
- 각 파일은 모범 문서와 주석 레이어로 구성한다. 주석은 문서의 어느 부분이 어느 원칙을 구현하는지 표시한다.
- 근거는 실제 한국어 좋은 문서의 분석이다(`exemplar-notes.md`: 링크, 잘한 점 → 원칙 ID, 일본 글의 구조 패턴).
- 원문은 넣지 않는다.
- 본보기 자체도 §8 검증을 통과해야 한다.
- 문서 작성 시에는 선택한 유형의 본보기 한 편만 로드한다.
- 수집 결과, 기존 국내 트러블슈팅 문서에는 해결 확인 단계가 없다. 모범 문서에서 이 단계를 보강한다.

## 10. 테스트

skill-creator 방식으로 평가한다. 시나리오마다 스킬을 쓴 경우와 쓰지 않은 경우를 비교한다.

1. 코드 저장소에서 README 작성 (Markdown)
2. 장애 메모와 로그로 포스트모템 작성 (타임라인 표, 다이어그램)
3. 기존 설계 문서 개선 (개선 모드, 구조 제안 게이트)
4. 같은 기능을 PM 독자용으로 설명 (eli5 연동)
5. Confluence 형식 출력 (mmdc 렌더링)
6. config가 없는 상태에서 최초 실행 (인터뷰 → config 생성)

## 11. 라이선스

- Toss 가이드와 easy-vibe는 CC BY-NC-SA 4.0이다. 인포그랩 글에는 2차 가공 금지 고지가 있다.
- 스킬에는 원문 문장과 예시를 옮기지 않는다. 원칙은 다시 쓰고, 예시는 새로 만든다.
- 출처 목록은 README에 표기한다.
- 본보기 원문도 링크와 분석만 둔다.

## 12. 구현 순서와 사용자 게이트

1. 본보기 후보 통합 → **사용자가 최종 본보기 선정**
2. references 작성 (원칙 → 예시 → 템플릿)
3. 본보기 9편 작성과 검증
4. SKILL.md, config, 스크립트 작성
5. 플러그인 매니페스트 작성, 로컬 설치
6. §10 평가와 개선
