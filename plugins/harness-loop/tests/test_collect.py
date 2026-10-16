import json
import shutil
import subprocess
from pathlib import Path

import collect
import state
import transcript
from transcript import Call, ToolError, Turn

FIX = Path(__file__).parent / "fixtures"
FIXTURE_403_TEXT = "Error: the app is not installed for this site"
FIXTURE_BASH_TEXT = "error: unknown flag --json"
FIXTURE_FIGMA_TEXT = "MCP error -32602: Invalid arguments for tool get_design_context: nodeId must match pattern 123:456"


def turns(name):
    return transcript.parse(FIX / name)[0]


def by_cond(sigs, cond):
    return [x for x in sigs if x["condition"] == cond]


def sigs_of(ts, kind="normal"):
    return [x["sig"] for x in collect.signals(ts, kind)]


def copy_fixture(root, name):
    t = root / "proj" / name
    t.parent.mkdir(exist_ok=True)
    shutil.copy(FIX / name, t)
    return t


def T(index, user, calls=(), errors=(), ai_tail="", cwd="/tmp/proj", **kw):
    return Turn(index, cwd, index * 10, user, list(calls), list(errors), ai_tail=ai_tail, **kw)


def bash(cid, cmd, skill=None):
    return Call(cid, "Bash", {"command": cmd}, skill)


def test_normalize_error():
    assert collect.normalize_error("<tool_use_error>File /Users/a/b.md not found at line 12</tool_use_error>") == "File P not found at line N"


def test_c1_retry_topic_normalized():
    s = by_cond(collect.signals(turns("basic.jsonl"), "normal"), "C1")
    assert [x["sig"] for x in s] == ["C1:" + collect.normalize_error(FIXTURE_403_TEXT)]


def test_permission_and_exit_code_errors_are_noise():
    # signals.jsonl 픽스처에는 권한 오류 1건, "Exit code 1" 단독 오류 1건, 메시지가 붙은 Bash 오류 1건, Figma 검증 오류 1건이 있다
    assert [x["sig"] for x in by_cond(collect.signals(turns("signals.jsonl"), "normal"), "C1")] == ["C1:" + collect.normalize_error(FIXTURE_BASH_TEXT), "C1:" + collect.normalize_error(FIXTURE_FIGMA_TEXT)]


def test_c1_error_without_following_call_is_not_signal():
    t = T(1, "해줘", [bash("a", "x")], [ToolError("a", "Bash", "boom")])
    assert by_cond(collect.signals([t], "normal"), "C1") == []


def test_c1_running_skill_edits_own_file():
    own = Call("e", "Edit", {"file_path": "/s/skills/ticket-creator/SKILL.md"}, "ticket-creator")
    other = Call("w", "Write", {"file_path": "/s/skills/eli5/SKILL.md"}, "ticket-creator")
    assert sigs_of([T(1, "해줘", [own])]) == ["C1:skill-self-edit"]
    assert sigs_of([T(1, "해줘", [other])]) == []


def test_c4_bare_yes_after_question():
    assert any(x["sig"] == "C4:bare-yes" for x in collect.signals(turns("signals.jsonl"), "normal"))


def test_c4_long_answer_is_not_bare_yes():
    ts = [T(1, "해줘", ai_tail="생성할까요?"), T(2, "네 그런데 제목은 바꾸고 담당자도 나로 지정하고 에픽도 옮겨줘")]
    assert "C4:bare-yes" not in sigs_of(ts)


def test_c4_question_word_prefix_is_not_bare_yes():
    for reply in ("어디까지 했어?", "예시 하나 더", "진행 상황은?", "네이버로 바꿔", "네？"):
        assert "C4:bare-yes" not in sigs_of([T(1, "해줘", ai_tail="생성할까요?"), T(2, reply)])
    assert "C4:bare-yes" in sigs_of([T(1, "해줘", ai_tail="생성할까요?"), T(2, "네 진행해")])


def test_c4_asked_after_no_ask():
    assert "C4:asked-after-no-ask" in sigs_of([T(1, "묻지 말고 끝까지 해", ai_tail="다음 단계로 갈까요?")])
    assert "C4:asked-after-no-ask" not in sigs_of([T(1, "끝까지 해", ai_tail="다음 단계로 갈까요?")])


def test_worker_has_no_c2_c4_c9():
    sig = collect.signals(turns("signals.jsonl"), "worker")
    assert not {x["condition"] for x in sig} & {"C2", "C4", "C9"}


def test_worker_collects_only_c1_c3_c10():
    edit = Call("e", "Edit", {"file_path": "/tmp/a"}, None)
    ts = [T(1, "ticket-creator 스킬로 고쳐줘", [edit], ai_tail="앞서 말한 원인은 가정이었습니다."), T(2, "아니 되돌려줘"), T(3, "끝났어?")]
    assert {x["condition"] for x in collect.signals(ts, "normal")} >= {"C2", "C5", "C6", "C8", "C10", "C11"}
    assert {x["condition"] for x in collect.signals(ts, "worker")} == {"C10"}
    assert {x["condition"] for x in collect.signals(turns("compacted.jsonl"), "worker")} == set()


def test_c2_c7_correction_rejection_and_noise():
    s = collect.signals(turns("compacted.jsonl"), "normal")
    assert [(x["sig"], x["turn"]) for x in by_cond(s, "C2")] == [("C2:?", 1), ("C2:?", 2)]
    assert by_cond(s, "C7") == []   # C2·C7 은 C2:? 후보 하나로 모으고 judge 가 condition 을 고른다
    assert all(x["needs_judge"] for x in by_cond(s, "C2"))
    assert by_cond(collect.signals([T(1, "해줘"), T(2, "좋아 다음 거 해줘")], "normal"), "C2") == []


def test_c1_empty_error_topic_is_not_signal():
    t = T(1, "해줘", [bash("a", "x"), bash("b", "y")], [ToolError("a", "Bash", "")])
    assert by_cond(collect.signals([t], "normal"), "C1") == []


def test_c3_repeated_command_and_trivial_excluded():
    sig = [x["sig"] for x in collect.signals(turns("signals.jsonl"), "normal") if x["condition"] == "C3"]
    assert "C3:cmd:acli jira workitem" in sig and not any(s.startswith("C3:cmd:cat") for s in sig)


def test_c3_cmd_once_at_first_occurrence():
    s = [x for x in collect.signals(turns("signals.jsonl"), "normal") if x["sig"] == "C3:cmd:acli jira workitem"]
    assert [(x["turn"], x["line"]) for x in s] == [(1, 1)]


def test_c3_inline_helper_and_skill_preamble():
    helper = bash("a", "python3 - <<'EOF'\ndef f(x):\n    return x\nEOF")
    assert "C3:inline-helper" in sigs_of([T(1, "해줘", [helper])])
    assert "C3:inline-helper" not in sigs_of([T(1, "해줘", [bash("a", "python3 -c 'print(1)'")])])
    read = Call("r", "Read", {"file_path": "/s/ticket-creator/references/types.md"}, "ticket-creator")
    plain = Call("r", "Read", {"file_path": "/s/ticket-creator/references/types.md"}, None)
    assert "C3:skill-preamble:ticket-creator" in sigs_of([T(1, "해줘", [read])])
    assert sigs_of([T(1, "해줘", [plain])]) == []


def test_command_signature():
    assert collect.command_signature("cd /x && acli jira workitem view IBS-1 --json") == "acli jira workitem"
    assert collect.command_signature("python3 - <<'EOF'\nimport json, yaml\nEOF") == "py-inline[yaml]"


def test_c5_retraction():
    s = collect.signals([T(1, "확인해줘", ai_tail="앞서 말한 원인은 가정이었습니다.")], "normal")
    assert [(x["sig"], x["needs_judge"]) for x in by_cond(s, "C5")] == [("C5:?", True)]
    assert by_cond(collect.signals([T(1, "확인해줘", ai_tail="원인을 찾았습니다.")], "normal"), "C5") == []


def test_c6_rejected_edit_and_revert_request():
    assert "C6:?" in sigs_of(turns("compacted.jsonl"))
    edit = Call("e", "Edit", {"file_path": "/tmp/a"}, None)
    assert "C6:?" in sigs_of([T(1, "고쳐줘", [edit]), T(2, "그거 되돌려줘")])
    assert "C6:?" not in sigs_of([T(1, "고쳐줘", [bash("a", "ls")]), T(2, "그거 되돌려줘")])


def test_c8_named_skill_not_invoked():
    s = by_cond(collect.signals([T(1, "ticket-creator 스킬로 만들어줘")], "normal"), "C8")
    assert [(x["sig"], x["skill"]) for x in s] == [("C8:skill:ticket-creator", "ticket-creator")]
    assert "C8:skill:eli5" in sigs_of([T(1, "/eli5 이거 설명해줘")])
    skill = Call("s", "Skill", {"skill": "eli5"}, "eli5")
    assert sigs_of([T(1, "/eli5 이거 설명해줘", [skill])]) == []
    assert by_cond(collect.signals([T(1, "/tmp/proj/a.md 열어줘")], "normal"), "C8") == []


def test_c8_codegraph_unused(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".codegraph").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    greps = [bash(f"g{i}", f"grep -rn foo{i} src") for i in range(5)]
    assert "C8:codegraph-unused" in sigs_of([T(1, "찾아줘", greps, cwd=str(repo))])
    assert "C8:codegraph-unused" not in sigs_of([T(1, "찾아줘", greps[:4], cwd=str(repo))])
    assert "C8:codegraph-unused" not in sigs_of([T(1, "찾아줘", greps, cwd=str(tmp_path / "missing"))])


def test_c9_quotes_previous_answer():
    assert "C9:?" in sigs_of(turns("signals.jsonl"))
    assert "C9:?" not in sigs_of([T(1, "해줘", ai_tail="짧은 답"), T(2, "짧은 답 말고 자세히")])


def test_c10_polling_and_status_ask():
    polls = [bash("a", "sleep 30"), Call("m", "Monitor", {}, None), bash("b", "herdr agent wait w1")]
    assert "C10:polling" in sigs_of([T(1, "기다려", polls)])
    assert "C10:polling" not in sigs_of([T(1, "기다려", polls[:2])])
    assert "C10:status-ask" in sigs_of([T(1, "끝났어?")])


def test_c11_api_error_and_resend():
    assert "C11:api-error" in sigs_of(turns("compacted.jsonl"))
    assert "C11:resend" in sigs_of([T(1, "해줘"), T(2, "해줘")])
    assert "C11:resend" not in sigs_of([T(1, "해줘", ai_tail="했습니다"), T(2, "고마워")])


def test_signal_fields():
    x = by_cond(collect.signals(turns("basic.jsonl"), "normal"), "C1")[0]
    assert set(x) == {"sig", "condition", "turn", "line", "skill", "quote", "needs_judge"}
    assert (x["turn"], x["line"], x["skill"], x["needs_judge"]) == (1, 2, "ticket-creator", False)
    assert x["quote"] == FIXTURE_403_TEXT


def test_run_appends_and_moves_cursor(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    first = collect.run(t); second = collect.run(t)
    assert first and second == []
    assert len(open(state.harness_dir(t) / "signals.jsonl").readlines()) == len(first)


def test_run_resumes_with_only_new_signals(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    collect.run(t)
    with open(t, "a") as f:
        f.write(json.dumps({"type": "user", "cwd": "/tmp/proj", "message": {"role": "user", "content": "끝났어?"}}) + "\n")
    assert [x["sig"] for x in collect.run(t)] == ["C10:status-ask"]
    assert state.read_cursor(state.harness_dir(t)) == sum(1 for _ in open(t))


def test_eval_session_writes_skip(tmp_projects):
    t = copy_fixture(tmp_projects, "eval.jsonl")
    assert collect.run(t) == [] and (state.harness_dir(t) / "skip").exists()


def test_command_signature_strips_env_assignments():
    assert collect.command_signature("FOO=1 acli jira workitem view X") == "acli jira workitem"
    assert collect.command_signature('cd /x && A="b c" B=2 acli jira workitem view X') == "acli jira workitem"


def test_harness_loop_script_calls_are_not_c3():
    cmd = 'CLAUDE_PLUGIN_DATA="/x" uv run --script "/p/scripts/report.py" where'
    assert not [s for s in sigs_of([T(1, "해줘", [bash("a", cmd), bash("b", cmd)])]) if s.startswith("C3")]
    root = 'uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" digest t'
    inst = "uv run --script /h/.claude/plugins/harness-loop/scripts/report.py list"
    assert not [s for s in sigs_of([T(1, "해줘", [bash("a", root), bash("b", inst)])]) if s.startswith("C3")]
    other = "uv run --script tools/build.py"
    assert "C3:cmd:uv run --script" in sigs_of([T(1, "해줘", [bash("a", other), bash("b", other)])])


def test_worktree_isolation_denial_is_c1_noise():
    text = "This session is isolated in the worktree /w/x. Edit outside is blocked"
    t = T(1, "해줘", [bash("a", "x"), bash("b", "y")], [ToolError("a", "Bash", text)])
    assert by_cond(collect.signals([t], "normal"), "C1") == []


def test_permission_request_denial_is_c1_noise():
    text = "Claude requested permissions to write to /w/x.md, but you haven't granted it yet."
    t = T(1, "해줘", [bash("a", "x"), bash("b", "y")], [ToolError("a", "Bash", text)])
    assert by_cond(collect.signals([t], "normal"), "C1") == []


def test_ansi_escapes_stripped_from_c1_topic():
    body = "\x1b[31m[bat error]\x1b[0m: 'x': No such file"
    text = transcript._error_line(body)
    assert text == "[bat error]: 'x': No such file"
    t = T(1, "해줘", [bash("a", "cat x"), bash("b", "ls")], [ToolError("a", "Bash", text)])
    assert sigs_of([t]) == ["C1:[bat error]: 'x': No such file"]


def test_c8_slash_mention_only_plugin_form_or_utterance_start():
    assert by_cond(collect.signals([T(1, "ls /tmp 해줘")], "normal"), "C8") == []
    assert by_cond(collect.signals([T(1, "cat /nonexistent-probe-1 결과 알려줘")], "normal"), "C8") == []
    assert "C8:skill:harness-loop:retro" in sigs_of([T(1, "/harness-loop:retro 를 했는데")])
    assert "C8:skill:harness-loop:retro" in sigs_of([T(1, "아까 /harness-loop:retro 를 했는데")])
    assert "C8:skill:eli5" in sigs_of([T(1, "  /eli5 설명해줘")])
    assert [x["sig"] for x in by_cond(collect.signals([T(1, "/tmpfoo 말고 ls /tmp 해줘")], "normal"), "C8")] == ["C8:skill:tmpfoo"]


def test_queued_correction_is_c2_candidate():
    s = by_cond(collect.signals(turns("queued.jsonl"), "normal"), "C2")
    assert [(x["sig"], x["turn"], x["line"]) for x in s] == [("C2:?", 2, 4)]


def test_c11_resend_not_fired_around_queued_prompts():
    assert "C11:resend" not in sigs_of([T(1, "해줘", queued=True), T(2, "그리고 이것도", queued=True)])
    assert "C11:resend" not in sigs_of([T(1, "해줘"), T(2, "그리고 이것도", queued=True)])


def test_two_consecutive_queued_prompts_parse_and_no_resend(tmp_path):
    rows = [
        {"type": "user", "cwd": "/tmp/proj", "message": {"role": "user", "content": "빌드해줘"}},
        {"type": "assistant", "cwd": "/tmp/proj", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "make"}}]}},
        {"type": "attachment", "cwd": "/tmp/proj", "attachment": {"type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, "prompt": "테스트도 돌려줘"}},
        {"type": "attachment", "cwd": "/tmp/proj", "attachment": {"type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, "prompt": "린트도"}},
        {"type": "assistant", "cwd": "/tmp/proj", "message": {"role": "assistant", "content": [{"type": "text", "text": "모두 끝냈습니다."}]}},
    ]
    p = tmp_path / "q2.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    ts = transcript.parse(p)[0]
    assert [(t.user, t.queued) for t in ts] == [("빌드해줘", False), ("테스트도 돌려줘", True), ("린트도", True)]
    assert "C11:resend" not in sigs_of(ts)


def test_c1_retry_after_queued_prompt_is_signal(tmp_path):
    rows = [
        {"type": "user", "cwd": "/tmp/proj", "message": {"role": "user", "content": "빌드해줘"}},
        {"type": "assistant", "cwd": "/tmp/proj", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "make build"}}]}},
        {"type": "user", "cwd": "/tmp/proj", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b1", "content": "make: *** No rule to make target build", "is_error": True}]}},
        {"type": "attachment", "cwd": "/tmp/proj", "attachment": {"type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, "prompt": "테스트도 돌려줘"}},
        {"type": "assistant", "cwd": "/tmp/proj", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "b2", "name": "Bash", "input": {"command": "make all"}}]}},
    ]
    p = tmp_path / "q3.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    s = by_cond(collect.signals(transcript.parse(p)[0], "normal"), "C1")
    assert [(x["sig"], x["turn"]) for x in s] == [("C1:make: *** No rule to make target build", 1)]
    t = T(1, "해줘", [bash("a", "x")], [ToolError("a", "Bash", "boom")])
    assert by_cond(collect.signals([t, T(2, "다른 거", [bash("b", "y")])], "normal"), "C1") == []
