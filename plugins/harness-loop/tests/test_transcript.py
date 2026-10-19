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
    assert [(t.user, t.slash) for t in turns] == [("/clear", True), ("README 고쳐줘", False), ("다시 해봐", False)]
    first, second = turns[1:]
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
    assert [e.text for e in turns[0].errors if e.tool == "Bash"] == ["This command requires approval", "", "error: unknown flag --json"]


def test_error_line_skips_symbol_only_and_shebang_lines():
    assert transcript._error_line("---\n{\nreal error: x\n}") == "real error: x"
    assert transcript._error_line("Exit code 2\n---\nboom") == "boom"
    assert transcript._error_line("#!/bin/bash\nfoo: not found") == "foo: not found"


def test_error_line_empty_when_every_line_skipped():
    for body in ("---", "Exit code 1", "### 제목", "Exit code 1\n# 개발 티켓\n}"):
        assert transcript._error_line(body) == ""


def test_error_line_skips_headings_and_ls_listing():
    assert transcript._error_line("### Error\nfoo failed") == "foo failed"
    assert transcript._error_line("# 개발 티켓\n본문 첫 줄") == "본문 첫 줄"
    assert transcript._error_line("#제목없음 붙은 해시") == "#제목없음 붙은 해시"
    ls = "drwxr-xr-x  5 u  staff  160 Oct 16 12:00 .\n-rw-r--r--  1 u  staff  10 Oct 16 12:00 a.txt\nls: b: No such file or directory"
    assert transcript._error_line(ls) == "ls: b: No such file or directory"
    assert transcript._error_line("total 24\n" + ls) == "ls: b: No such file or directory"
    assert transcript._error_line("lrwxr-xr-x@ 1 u  staff  10 Oct 16 12:00 l -> a\ndrwxrwxrwt+ 2 u  staff  64 Oct 16 12:00 t\nboom") == "boom"
    assert transcript._error_line("---------- ERROR: x ----------\nmore") == "---------- ERROR: x ----------"


def test_error_line_skips_ls_listing_without_link_count():
    assert transcript._error_line("drwxr-xr-x yeonjin.roh wheel 160 B Fri Oct 16 12:00:00 2026 .\nboom") == "boom"
    assert transcript._error_line("drwxr-xr-x  5 u  staff  160 Oct 16 12:00 .\nboom") == "boom"
    assert transcript._error_line("-rw-r--r--@ u staff 10 B a\nboom") == "boom"
    assert transcript._error_line("---------- ERROR: x ----------\nmore") == "---------- ERROR: x ----------"
    assert transcript._error_line("---------- x\nmore") == "---------- x"
    assert transcript._error_line(".rw-r--r-- yeonjin.roh wheel 10 B Fri Oct 16 12:00:00 2026 a\nboom") == "boom"
    assert transcript._error_line(".---------- x\nmore") == ".---------- x"


def test_error_line_traceback_takes_last_exception_line():
    tb = "Traceback (most recent call last):\n  File \"x.py\", line 3, in <module>\nKeyError: 'a'"
    assert transcript._error_line(tb) == "KeyError: 'a'"
    assert transcript._error_line("Traceback (most recent call last):\n  File \"x.py\", line 3, in <module>\nException: boom") == "Exception: boom"
    assert transcript._error_line("Traceback (most recent call last):\n  File \"x.py\", line 3\n    raise ValueError(\"bad\")\nError: x") == "Error: x"
    assert transcript._error_line("Traceback (most recent call last):\n  File \"x.py\", line 3\n    raise ValueError(\"bad\")") == "Traceback (most recent call last):"
    assert transcript._error_line("Exit code 1\nTraceback (most recent call last):\n  File \"x.py\", line 3") == "Traceback (most recent call last):"
    assert transcript._error_line("Traceback (most recent call last):\nSystemExit: 1\nExit code 2") == "SystemExit: 1"
    chained = "Exit code 1\nTraceback (most recent call last):\nValueError: first\n\nDuring handling...\nTraceback (most recent call last):\n  File \"y.py\"\nmod.CustomException: last"
    assert transcript._error_line(chained) == "mod.CustomException: last"
    assert transcript._error_line("Traceback (most recent call last):\n  File \"x.py\", line 3") == "Traceback (most recent call last):"


def test_queued_command_prompt_starts_new_human_turn():
    turns, _ = transcript.parse(FIX / "queued.jsonl")
    assert [(t.index, t.line, t.cwd, t.user) for t in turns] == [
        (1, 1, "/tmp/proj", "README 의 설치 절을 고쳐줘"),
        (2, 4, "/tmp/proj", "아니, 그게 아니라 사용법 절이야"),
        (3, 9, "/tmp/proj/sub", "예시도 하나 넣어줘"),
    ]
    assert [c.name for c in turns[0].calls] == ["Bash"]
    assert [c.name for c in turns[1].calls] == ["Edit"]
    assert turns[2].ai_tail == "사용법 절을 고치고 예시를 넣었습니다."


DOC_THEN_ERROR = "Exit code 1\n# 개발 티켓\n\n본문 첫 줄입니다\n둘째 줄\ngrep: x: No such file or directory"


def test_bash_error_line_prefers_last_error_pattern_line():
    assert transcript._error_line(DOC_THEN_ERROR, "Bash") == "grep: x: No such file or directory"
    assert transcript._error_line("Exit code 1\nwarning: a failed\nok\nfatal: not a git repository", "Bash") == "fatal: not a git repository"
    assert transcript._error_line("Exit code 2\nusage: x\nCan't open file", "Bash") == "Can't open file"
    assert transcript._error_line("Exit code 1\nconnection TIMED OUT after 5s\ndone", "Bash") == "connection TIMED OUT after 5s"


def test_bash_error_line_word_match_only():
    # `ValueError`·`errors`·`notfound` 는 단어 일치가 아니다 → 기존 규칙(Traceback 마지막 예외 줄, 아니면 첫 줄)
    tb = "Traceback (most recent call last):\n  File \"x.py\", line 3\nValueError: bad"
    assert transcript._error_line(tb, "Bash") == "ValueError: bad"
    assert transcript._error_line("Exit code 1\nfirst line\n3 errors found here", "Bash") == "first line"


def test_bash_doc_only_output_keeps_first_remaining_line():
    assert transcript._error_line("Exit code 1\n# 개발 티켓\n\n본문 첫 줄입니다\n둘째 줄", "Bash") == "본문 첫 줄입니다"


def test_non_bash_error_line_unchanged():
    assert transcript._error_line(DOC_THEN_ERROR, "Read") == "본문 첫 줄입니다"
    assert transcript._error_line(DOC_THEN_ERROR) == "본문 첫 줄입니다"


def test_parse_bash_doc_then_error_topic(tmp_path):
    import json

    lines = [
        {"type": "user", "message": {"role": "user", "content": "티켓 찾아줘"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cat t.md; grep x y"}},
            {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/t.md"}},
        ]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "b1", "content": DOC_THEN_ERROR, "is_error": True},
            {"type": "tool_result", "tool_use_id": "r1", "content": DOC_THEN_ERROR, "is_error": True},
        ]}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lines))
    turns, _ = transcript.parse(p)
    assert [(e.tool, e.text) for e in turns[0].errors] == [("Bash", "grep: x: No such file or directory"), ("Read", "본문 첫 줄입니다")]


def test_slash_command_starts_human_turn():
    turns, _ = transcript.parse(FIX / "slash.jsonl")
    assert [(t.index, t.line, t.user, t.slash) for t in turns] == [
        (1, 1, "README 의 설치 절을 고쳐줘", False),
        (2, 3, "/harness-loop:retro review", True),
        (3, 8, "/model", True),
        (4, 11, "고마워", False),
    ]
    assert [c.name for c in turns[1].calls] == ["Bash"] and turns[1].ai_tail == "검토 대기 항목 없음"
    assert turns[0].ai_tail == "설치 절을 고쳤습니다."


def test_slash_command_from_peer_is_not_human_turn(tmp_path):
    import json

    row = {"type": "user", "origin": {"kind": "peer"}, "message": {"role": "user", "content": "<command-name>/x</command-name>"}}
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(row) + "\n")
    assert transcript.parse(p)[0] == []


def _rows_file(tmp_path, rows):
    import json

    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return p


def _u(text):
    return {"type": "user", "cwd": "/tmp/proj", "message": {"role": "user", "content": text}}


def _q(text):
    return {"type": "attachment", "cwd": "/tmp/proj", "attachment": {"type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, "prompt": text}}


def _say(text):
    return {"type": "assistant", "cwd": "/tmp/proj", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def test_resend_of_queued_prompt_is_merged_into_queued_turn(tmp_path):
    p = _rows_file(tmp_path, [_u("빌드해줘"), _q("테스트도 돌려줘"), _say("빌드했습니다."), _u("테스트도 돌려줘"), _say("테스트도 돌렸습니다.")])
    turns, _ = transcript.parse(p)
    assert [(t.user, t.queued) for t in turns] == [("빌드해줘", False), ("테스트도 돌려줘", True)]
    assert turns[1].ai_tail == "테스트도 돌렸습니다."


def test_resend_starting_with_queued_text_within_two_turns_is_dropped(tmp_path):
    p = _rows_file(tmp_path, [_u("빌드해줘"), _q("테스트도 돌려줘"), _q("린트도"), _u("테스트도 돌려줘\n그리고 결과 알려줘")])
    assert [t.user for t in transcript.parse(p)[0]] == ["빌드해줘", "테스트도 돌려줘", "린트도"]


def test_resend_not_dropped_beyond_two_turns_or_without_queued(tmp_path):
    p = _rows_file(tmp_path, [_u("빌드해줘"), _q("테스트도 돌려줘"), _u("다음"), _u("또 다음"), _u("테스트도 돌려줘")])
    assert [t.user for t in transcript.parse(p)[0]] == ["빌드해줘", "테스트도 돌려줘", "다음", "또 다음", "테스트도 돌려줘"]
    p = _rows_file(tmp_path, [_u("해줘"), _u("해줘")])
    assert [t.user for t in transcript.parse(p)[0]] == ["해줘", "해줘"]


def test_short_queued_prompt_does_not_swallow_turns(tmp_path):
    p = _rows_file(tmp_path, [_u("빌드해줘"), _q("네"), _u("네 그럼 배포도 해줘")])
    assert [t.user for t in transcript.parse(p)[0]] == ["빌드해줘", "네", "네 그럼 배포도 해줘"]


def test_queued_prefix_must_end_on_word_boundary(tmp_path):
    p = _rows_file(tmp_path, [_u("빌드해줘"), _q("테스트도 돌려줘"), _u("테스트도 돌려줘요 빨리")])   # 접두 뒤가 글자
    assert [t.user for t in transcript.parse(p)[0]] == ["빌드해줘", "테스트도 돌려줘", "테스트도 돌려줘요 빨리"]
    p = _rows_file(tmp_path, [_u("빌드해줘"), _q("테스트도 돌려줘"), _u("테스트도 돌려줘, 그리고 결과도")])
    assert [t.user for t in transcript.parse(p)[0]] == ["빌드해줘", "테스트도 돌려줘"]


def test_slash_plugin_skill_sets_skill_span():
    turns, _ = transcript.parse(FIX / "slash.jsonl")
    assert [c.skill for c in turns[1].calls] == ["harness-loop:retro"]


def test_slash_skill_span_from_later_skill_call_and_not_for_builtin(tmp_path):
    def call(cid, name, inp):
        return {"type": "assistant", "cwd": "/tmp/proj", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": cid, "name": name, "input": inp}]}}

    p = _rows_file(tmp_path, [
        _u("<command-name>/eli5</command-name>\n<command-args>x</command-args>"),
        call("r1", "Read", {"file_path": "/s/eli5/references/a.md"}),
        call("k1", "Skill", {"skill": "eli5"}),
        _u("<command-name>/model</command-name>"),
        call("r2", "Read", {"file_path": "/s/a.md"}),
    ])
    turns, _ = transcript.parse(p)
    assert [c.skill for c in turns[0].calls] == ["eli5", "eli5"]
    assert [c.skill for c in turns[1].calls] == [None]


def test_bash_traceback_rule_wins_over_error_word():
    tb2 = "Exit code 1\nTraceback (most recent call last):\n  File \"x.py\", line 3, in <module>\n    raise error(\"cannot open\")\nKeyError: 'a'"
    assert transcript._error_line(tb2, "Bash") == "KeyError: 'a'"


def test_resent_slash_command_marks_queued_turn_slash_and_opens_skill(tmp_path):
    read = {"type": "assistant", "cwd": "/tmp/proj", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/p/skills/retro/references/a.md"}}]}}
    p = _rows_file(tmp_path, [
        _u("빌드해줘"), _q("/harness-loop:retro review"), _say("빌드했습니다."),
        _u("<command-message>retro</command-message>\n<command-name>/harness-loop:retro</command-name>\n<command-args>review</command-args>"),
        read,
    ])
    turns, _ = transcript.parse(p)
    assert [(t.user, t.queued, t.slash) for t in turns] == [("빌드해줘", False, False), ("/harness-loop:retro review", True, True)]
    assert [c.skill for c in turns[1].calls] == ["harness-loop:retro"]
