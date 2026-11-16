import json
import subprocess

from conftest import SCRIPTS, load_script

redundancy = load_script("redundancy")


def kinds(report):
    return [f["kind"] for f in report["findings"]]


SPREAD = """# 승인 서비스

> **요약**
>
> - 승인은 세 번까지 다시 시도한다.

## 1. 구성 요소

| 구성 요소 | 하는 일 |
|---|---|
| `ApproveHandler` | 결제사에 승인을 요청한다 |

## 2. 흐름

`ApproveHandler`가 결제사를 부른다.

- `ApproveHandler`는 시간 초과면 다시 시도한다.

```mermaid
flowchart LR
  A["ApproveHandler"] --> B["결제사"]
```
"""


def test_identifier_spread_points_to_component_table_as_home():
    report = redundancy.analyze(SPREAD)
    f = next(x for x in report["findings"] if x["kind"] == "identifier-spread")
    assert f["subject"] == "ApproveHandler"
    assert f["home"]["line"] == 11 and f["home"]["layer"] == "표"
    assert [w["line"] for w in f["where"]] == [15, 17]
    assert "역할" in f["action"]
    assert report["metrics"]["identifier_max_layers"] >= 3


REPEAT = """# 세션

## 3. 핵심 결정

| 결정 | 이유 |
|---|---|
| 세션은 유저당 키 하나에 덮어쓴다 | 새 로그인이 덮어쓰므로 이전 리프레시 토큰은 저절로 무효가 된다 |

## 부록 A. 상세

| 결정 | 이유 |
|---|---|
| 키 하나 | 새 로그인이 덮어쓰므로 이전 리프레시 토큰은 저절로 무효가 된다 |
"""


def test_repeated_claim_keeps_body_and_flags_appendix():
    report = redundancy.analyze(REPEAT)
    f = next(x for x in report["findings"] if x["kind"] == "repeated-claim")
    assert f["home"]["line"] == 7
    assert [w["line"] for w in f["where"]] == [13]
    assert f["similarity"] >= 0.6
    assert "3. 핵심 결정" in f["action"]
    assert report["metrics"]["repeated_claims_strong"] == 1


SUMMARY_ONLY = """# 세션

> **요약**
>
> - 새 로그인이 덮어쓰므로 이전 리프레시 토큰은 저절로 무효가 된다.

## 1. 저장

새 로그인이 덮어쓰므로 이전 리프레시 토큰은 저절로 무효가 된다.
"""


def test_summary_repeating_body_is_not_flagged():
    assert "repeated-claim" not in kinds(redundancy.analyze(SUMMARY_ONLY))


TRANSCRIBED = """# 체인

## 1. 필터 체인

- `csrf`를 끈다
- `sessionManagement`를 무상태로 둔다
- `formLogin`, `httpBasic`, `logout`을 끈다

```java
http.csrf(c -> c.disable())
    .sessionManagement(s -> s.sessionCreationPolicy(STATELESS))
    .formLogin(f -> f.disable())
    .httpBasic(b -> b.disable())
    .logout(l -> l.disable());
```
"""


def test_code_transcribed_into_prose_is_flagged():
    f = next(x for x in redundancy.analyze(TRANSCRIBED)["findings"] if x["kind"] == "code-transcribed")
    assert f["where"][0]["line"] == 9
    assert f["ratio"] >= 0.6


CLEAN = """# 승인 서비스

결제 승인은 결제사가 시간 초과를 내면 세 번까지 다시 시도한다.

## 1. 구성 요소

| 구성 요소 | 하는 일 |
|---|---|
| `ApproveHandler` | 결제사에 승인을 요청한다 |

## 2. 장애 대응

결제사가 응답하지 않으면 주문은 보류 상태로 남고, 운영자가 재처리한다.
"""


def test_clean_document_has_no_findings():
    report = redundancy.analyze(CLEAN)
    assert report["findings"] == []
    assert report["metrics"]["identifier_max_layers"] <= 2


def test_cli_markdown_and_json(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text(REPEAT, encoding="utf-8")
    md = subprocess.run(["python3", str(SCRIPTS / "redundancy.py"), str(doc)], capture_output=True, text=True)
    assert md.returncode == 0 and "## 요약 지표" in md.stdout and "## 고칠 곳" in md.stdout
    js = subprocess.run(["python3", str(SCRIPTS / "redundancy.py"), str(doc), "--json"], capture_output=True, text=True)
    assert json.loads(js.stdout)["metrics"]["repeated_claims_strong"] == 1


SHARED_NAME = """# 전환

## 1. 티켓

| 티켓 | 맡은 것 |
|---|---|
| PAY-1 | Authorization Server 채택 결정과 도입 순서 |

## 2. 목표

| Authorization Server가 맡는 것 | 우리가 구현하는 것 |
|---|---|
| 토큰 엔드포인트와 클라이언트 인증 | 발급 요청 검증 |
"""


def test_table_header_and_shared_long_name_are_not_claims():
    report = redundancy.analyze(SHARED_NAME)
    assert [f for f in report["findings"] if f["kind"] in ("repeated-claim", "similar-claim")] == []


APPENDIX_TITLE = """# 승인

## 1. 구성 요소

| 구성 요소 | 하는 일 |
|---|---|
| `ApproveHandler` | 결제사에 승인을 요청한다 |

승인 처리기는 시간 초과면 세 번까지 다시 시도한다.

## 부록 A. 코드 발췌

<details>
<summary>ApproveHandler</summary>

```java
class ApproveHandler {}
```

</details>
"""


def test_appendix_code_titles_do_not_count_as_spread():
    report = redundancy.analyze(APPENDIX_TITLE)
    assert "identifier-spread" not in kinds(report)
    assert report["metrics"]["identifier_max_layers"] <= 2


PARAPHRASE = """# 서명

## 3. 핵심 결정

| 결정 | 이유 |
|---|---|
| 액세스 토큰과 리프레시 토큰을 같은 키로 서명한다 | 두 토큰은 토큰 종류로만 갈린다. 그래서 검증 서버가 토큰 종류를 반드시 확인한다 |

## 부록 A. 상세

| 결정 | 이유 |
|---|---|
| 액세스 토큰과 리프레시 토큰을 같은 키로 서명하고 토큰 종류로만 가른다 | 그래서 검증 서버가 토큰 종류를 반드시 확인한다 |
"""


def test_paraphrased_repeat_is_caught_with_morphemes():
    import pytest
    pytest.importorskip("kiwipiepy")
    report = redundancy.analyze(PARAPHRASE)
    assert report["metrics"]["similarity_mode"] == "4gram+kiwi"
    assert "repeated-claim" in kinds(report)


BOILERPLATE = """# 흐름

## 1. 로그인

다음 그림은 로그인 요청 한 번이 지나는 순서다.

## 2. 리프레시

다음 그림은 리프레시 요청 한 번이 지나는 순서다.
"""


def test_figure_intro_sentences_are_not_claims():
    report = redundancy.analyze(BOILERPLATE)
    assert [f for f in report["findings"] if f["kind"] in ("repeated-claim", "similar-claim")] == []


def test_falls_back_to_4gram_without_kiwi(monkeypatch):
    monkeypatch.setattr(redundancy, "_kiwi", lambda: None)
    report = redundancy.analyze(REPEAT)
    assert report["metrics"]["similarity_mode"] == "4gram"
    assert report["metrics"]["repeated_claims_strong"] == 1


REFERENCE_SECTION = """# 승인

## 1. 구성 요소

| 구성 요소 | 하는 일 |
|---|---|
| `ApproveHandler` | 결제사에 승인을 요청한다 |

## 참고

- [결정 기록](https://git.example.com/adr/0031.md) — `ApproveHandler` 재시도 도입
- [PR #318](https://git.example.com/pull/318) — `ApproveHandler` 시간 초과 처리

승인 처리기 설명은 1절에 있다.

> 요약: `ApproveHandler`가 재시도를 맡는다.
"""


def test_reference_section_does_not_count_as_spread():
    report = redundancy.analyze(REFERENCE_SECTION)
    assert "identifier-spread" not in kinds(report)
