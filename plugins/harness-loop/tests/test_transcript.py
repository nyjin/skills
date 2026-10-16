from pathlib import Path

import transcript

FIX = Path(__file__).parent / "fixtures"


def test_parse_basic_turns_and_errors():
    turns, last = transcript.parse(FIX / "basic.jsonl")
    assert [t.index for t in turns] == [1, 2]
    assert turns[0].errors[0].tool == "mcp__claude_ai_Atlassian__getJiraIssue"
    assert "app is not installed" in turns[0].errors[0].text
    assert turns[1].user.startswith("아니")
    assert last == sum(1 for _ in open(FIX / "basic.jsonl"))


def test_compaction_summary_is_not_user_turn():
    turns, _ = transcript.parse(FIX / "compacted.jsonl")
    assert all(not t.user.startswith("This session is being continued") for t in turns)


def test_partial_last_line_not_consumed():
    turns, last = transcript.parse(FIX / "partial.jsonl")
    assert last == sum(1 for _ in open(FIX / "partial.jsonl")) - 1


def test_start_line_skips_processed_turns():
    _, last = transcript.parse(FIX / "basic.jsonl")
    turns, _ = transcript.parse(FIX / "basic.jsonl", start_line=last)
    assert turns == []


def test_skill_span_survives_meta_injection():
    turns, _ = transcript.parse(FIX / "basic.jsonl")
    assert {c.skill for c in turns[0].calls if c.name == "Bash"} == {"ticket-creator"}


def test_session_kind():
    cfg = {"eval_markers": ["트리거 측정 중"]}
    assert transcript.session_kind(transcript.parse(FIX / "eval.jsonl")[0], cfg) == "eval"
    assert transcript.session_kind(transcript.parse(FIX / "worker.jsonl")[0], cfg) == "worker"
    assert transcript.session_kind(transcript.parse(FIX / "basic.jsonl")[0], cfg) == "normal"


def test_pasted_content_is_user_turn():
    turns, _ = transcript.parse(FIX / "worker.jsonl")   # 첫 사람 발화는 "<pasted_content id=…>## 격리 …"
    assert turns[0].user.startswith("<pasted_content")


def test_turn_fields_line_cwd_tail_and_skill_span_closes():
    turns, _ = transcript.parse(FIX / "basic.jsonl")
    assert (turns[0].line, turns[0].cwd) == (2, "/tmp/proj")
    assert (turns[1].line, turns[1].cwd) == (12, "/tmp/proj/sub")
    assert turns[0].ai_tail.endswith("생성할까요?")
    assert [c.name for c in turns[0].calls] == ["Skill", "mcp__claude_ai_Atlassian__getJiraIssue", "Bash"]
    assert turns[0].errors[0].call_id == "t2" and "\n" not in turns[0].errors[0].text
    assert turns[1].calls == [] and turns[1].ai_tail == "에픽 밑으로 옮기겠습니다."


def test_rejection_interrupt_sidechain_and_api_error():
    turns, _ = transcript.parse(FIX / "compacted.jsonl")
    assert [t.user for t in turns] == ["README 고쳐줘", "다시 해봐"]
    first, second = turns
    assert first.rejected == ["e1"] and first.errors == []
    assert first.interrupted is True and second.interrupted is False
    assert [c.name for c in first.calls] == ["Edit"]
    assert second.api_error is True and second.ai_tail == ""
    assert first.api_error is False


def test_resume_mid_turn_reemits_whole_turn():
    turns, _ = transcript.parse(FIX / "basic.jsonl", start_line=5)   # 5번 줄은 스킬 본문 주입, 오류 결과(7번 줄) 앞
    first = turns[0]
    assert first.index == 1 and first.line == 2
    assert first.errors[0].tool == "mcp__claude_ai_Atlassian__getJiraIssue"
    assert {c.skill for c in first.calls if c.name == "Bash"} == {"ticket-creator"}
    assert [t.index for t in turns] == [1, 2]


def test_resume_after_turn_keeps_session_index():
    turns, _ = transcript.parse(FIX / "basic.jsonl", start_line=11)   # 1번 턴은 11번 줄까지
    assert [(t.index, t.user[:2]) for t in turns] == [(2, "아니")]


def test_bash_error_text_skips_exit_code_line():
    turns, _ = transcript.parse(FIX / "signals.jsonl")
    assert [e.text for e in turns[0].errors if e.tool == "Bash"] == ["This command requires approval", "Exit code 1", "error: unknown flag --json"]
