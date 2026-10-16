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

## 라이선스

MIT
