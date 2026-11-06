import json
import os
import multiprocessing
import subprocess
from pathlib import Path

import pytest

import evidence
import paths
import versions

FIXTURES = Path(__file__).parent / "fixtures" / "payloads"
SID = "abcd1234-0000-0000-0000-000000000000"


def fixture(name, **over):
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    data.update(over)
    return data


@pytest.fixture
def session(tmp_projects):
    p = tmp_projects / "-proj" / f"{SID}.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
    return p


def payload(event, session, cwd=None, **kw):
    base = fixture(event)
    base["hook_event_name"] = event.split("-")[0]
    base.update(transcript_path=str(session), cwd=str(cwd or session.parent), **kw)
    return base


def tool_result(content, is_error=False):
    return json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": is_error, "content": content}]}}) + "\n"


def prompt(session, text, **kw):
    kw.setdefault("hash_of", versions.blob_hash)
    return evidence.handle("UserPromptSubmit", payload("UserPromptSubmit", session, prompt=text), **kw)


def test_prompt_record_fields(session):
    [r] = prompt(session, "아니 그거 말고")
    assert r["kind"] == "prompt" and r["text"] == "아니 그거 말고" and r["pos"] == session.stat().st_size
    assert r["id"] == "abcd1234-1" and r["skill"] is None and r["session"] == SID
    assert r["transcript"] == str(session) and r["file"] is None
    assert r["flags"]["after_rejection"] is False
    lines = (paths.harness_path(session) / "evidence.jsonl").read_text().splitlines()
    assert json.loads(lines[0]) == r
    [r2] = prompt(session, "다음")
    assert r2["id"] == "abcd1234-2"


def test_text_truncated(session):
    [r] = prompt(session, "가" * 3000)
    assert len(r["text"]) == 2000


def test_injected_and_continued_prompts_not_recorded(session):
    assert prompt(session, "<task-notification>…") == []
    assert prompt(session, "This session is being continued from a previous conversation …") == []
    assert not (paths.harness_path(session) / "evidence.jsonl").exists()


def test_eval_marker_creates_skip_and_stops(session):
    assert prompt(session, "트리거 측정 중 입니다") == []
    assert (paths.harness_path(session) / "skip").exists()
    assert prompt(session, "다시") == []
    assert evidence.handle("PreToolUse", fixture("PreToolUse-Skill", transcript_path=str(session))) == []


def test_guards(session, monkeypatch):
    p = payload("UserPromptSubmit", session, prompt="x", agent_id="a1")
    assert evidence.handle("UserPromptSubmit", p) == []
    monkeypatch.setenv("HARNESS_RETRO", "1")
    assert prompt(session, "x") == []


def test_skill_state_from_skill_call_and_base_dir(session, tmp_path):
    sk = tmp_path / "plug" / "skills" / "write"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("# write\n")
    pos = session.stat().st_size
    with open(session, "a") as f:
        f.write(tool_result(f"Launching skill\nBase directory for this skill: {sk}\n\nbody"))
    [s] = evidence.handle("PreToolUse", fixture("PreToolUse-Skill", transcript_path=str(session), cwd=str(tmp_path)),
                          pos=pos, hash_of=versions.blob_hash)
    assert s["kind"] == "skill_start" and s["skill"]["name"] == "tech-writing:write"
    [r] = prompt(session, "다시 해")
    assert r["skill"]["name"] == "tech-writing:write" and r["skill"]["prompts_since"] == 1
    assert r["skill"]["path"].endswith("skills/write") and r["skill"]["hash"] == versions.blob_hash(sk / "SKILL.md")
    assert "guidelines" not in r["meta"]
    state = evidence.skill_state(paths.harness_path(session))
    assert state["path"].endswith("skills/write") and state["prompts_since"] == 1
    [r2] = prompt(session, "또")
    assert r2["skill"]["prompts_since"] == 2


def test_slash_prompt_sets_skill(session):
    [r] = prompt(session, "/tech-writing:write README")
    assert evidence.skill_state(paths.harness_path(session))["name"] == "tech-writing:write"
    assert r["skill"]["name"] == "tech-writing:write"


def test_prompt_expansion_defensive(session):
    base = payload("UserPromptSubmit", session)
    base["hook_event_name"] = "UserPromptExpansion"
    base.pop("prompt")
    assert evidence.handle("UserPromptExpansion", base) == []
    assert evidence.skill_state(paths.harness_path(session)) is None
    [r] = evidence.handle("UserPromptExpansion", {**base, "command_name": "tech-writing:write"})
    assert r["kind"] == "skill_start" and r["skill"]["name"] == "tech-writing:write"
    [r] = evidence.handle("UserPromptExpansion", {**base, "prompt": "/foo:bar x"})
    assert r["skill"]["name"] == "foo:bar"


def test_prompt_outside_skill_records_guideline_hashes(session, tmp_path):
    proj = tmp_path / "repo"
    (proj / ".claude" / "rules").mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("rules\n")
    (proj / ".claude" / "rules" / "a.md").write_text("a\n")
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    (tmp_path / "home" / ".claude" / "CLAUDE.md").write_text("global\n")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    [r] = evidence.handle("UserPromptSubmit", payload("UserPromptSubmit", session, cwd=proj, prompt="hi"),
                          hash_of=versions.blob_hash)
    got = {Path(g["path"]).resolve(): g["hash"] for g in r["meta"]["guidelines"]}
    assert got == {
        (proj / "CLAUDE.md").resolve(): versions.blob_hash(proj / "CLAUDE.md"),
        (proj / ".claude/rules/a.md").resolve(): versions.blob_hash(proj / ".claude/rules/a.md"),
        (tmp_path / "home/.claude/CLAUDE.md").resolve(): versions.blob_hash(tmp_path / "home/.claude/CLAUDE.md"),
    }


def test_no_guideline_files_no_entries(session, tmp_path):
    (tmp_path / "empty").mkdir()
    [r] = evidence.handle("UserPromptSubmit", payload("UserPromptSubmit", session, cwd=tmp_path / "empty", prompt="hi"),
                          hash_of=versions.blob_hash)
    assert r["meta"].get("guidelines", []) == []


def test_after_rejection_flag(session):
    with open(session, "a") as f:
        f.write(tool_result("The user doesn't want to proceed with this tool use.", True))
    [r] = prompt(session, "그 파일은 건드리지 마")
    assert r["flags"]["after_rejection"] is True


def test_after_rejection_only_last_result(session):
    with open(session, "a") as f:
        f.write(tool_result("The user doesn't want to proceed", True))
        f.write(tool_result("ok"))
    assert evidence.after_rejection(session, session.stat().st_size) is False
    assert evidence.after_rejection(Path("/nonexistent"), 10) is False


def test_missing_transcript_records_pos_zero(tmp_projects):
    t = tmp_projects / "-proj" / f"{SID}.jsonl"
    [r] = evidence.handle("UserPromptSubmit", payload("UserPromptSubmit", t, prompt="hi"), hash_of=versions.blob_hash)
    assert r["pos"] == 0


def bash(cmd, session):
    return payload("PreToolUse-Bash", session, tool_input={"command": cmd})


def _worker(args):
    session, n = args
    for i in range(n):
        evidence.handle("PreToolUse", bash(f"echo {i}", Path(session)), hash_of=versions.blob_hash)


def test_concurrent_appends_stay_valid_jsonl(session):
    with multiprocessing.Pool(8) as pool:
        pool.map(_worker, [(str(session), 50)] * 8)
    lines = (paths.harness_path(session) / "evidence.jsonl").read_text().splitlines()
    rows = [json.loads(ln) for ln in lines]
    assert len(rows) == 400
    assert len({r["id"] for r in rows}) == 400


def test_never_matching_skill_stops_rescanning(session):
    with open(session, "a") as f:
        f.write(tool_result("x" * (evidence.BASE_SCAN + 10)))
    [r] = prompt(session, "/some:skill go", pos=0)
    hdir = paths.harness_path(session)
    state = evidence.skill_state(hdir)
    assert state["base_dir_checked"] is True and not state["path"]
    assert not r["skill"]["path"]
    sk = session.parent / "late" / "skills" / "x"
    with open(session, "a") as f:
        f.write(tool_result(f"Base directory for this skill: {sk}"))
    [r2] = prompt(session, "again")
    assert not r2["skill"]["path"] and not evidence.skill_state(hdir)["path"]


def test_guideline_hash_cached_when_unchanged(session, tmp_path):
    proj = tmp_path / "repo2"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("a\n")
    calls = []

    def stub(p):
        calls.append(str(p))
        return "h"

    def go():
        return evidence.handle("UserPromptSubmit", payload("UserPromptSubmit", session, cwd=proj, prompt="hi"), hash_of=stub)

    go()
    n = len(calls)
    assert n >= 1
    [r] = go()
    assert len(calls) == n and r["meta"]["guidelines"][0]["hash"] == "h"
    (proj / "CLAUDE.md").write_text("changed content\n")
    go()
    assert len(calls) == n + 1


def test_after_rejection_false_when_big_line_straddles_window(session):
    with open(session, "a") as f:
        f.write(tool_result("The user doesn't want to proceed", True))
        f.write(tool_result("y" * 70000))
        f.write(json.dumps({"type": "user", "note": "tool_result"}) + "\n")
    assert evidence.after_rejection(session, session.stat().st_size) is False


def test_after_rejection_true_when_window_cuts_ordinary_line(session):
    with open(session, "a") as f:
        f.write(tool_result("z" * 70000))
        f.write(tool_result("The user doesn't want to proceed", True))
    assert evidence.after_rejection(session, session.stat().st_size) is True


def test_slash_start_prompt_has_prompts_since_zero(session):
    [r] = prompt(session, "/tech-writing:write README")
    assert r["skill"]["prompts_since"] == 0


@pytest.mark.parametrize("cmd,body", [
    ("python3 - <<'EOF'\nprint(1)\nEOF", "print(1)\n"),
    ('python3 -c "import json; print(2)"', "import json; print(2)"),
    ("cat <<-X\n\tsome\nX", "\tsome\n"),
    ('cat <<"E"\na\nE', "a\n"),
    ("cat <<E\na\nE", "a\n"),
    ("ls -la", None),
])
def test_inline_body(cmd, body):
    assert evidence.inline_body(cmd) == body


def test_bash_saves_inline_script_once(session):
    cmd = "python3 - <<'EOF'\nprint(1)\nEOF"
    [a] = evidence.handle("PreToolUse", bash(cmd, session), hash_of=versions.blob_hash)
    [b] = evidence.handle("PreToolUse", bash(cmd, session), hash_of=versions.blob_hash)
    assert a["kind"] == "bash" and a["text"] == cmd
    assert a["file"] == b["file"]
    assert len(list((paths.harness_path(session) / "scripts").iterdir())) == 1
    assert Path(a["file"]).read_bytes() == b"print(1)\n"


def test_plain_bash_has_no_file(session):
    [r] = evidence.handle("PreToolUse", bash("ls -la", session), hash_of=versions.blob_hash)
    assert r["file"] is None and r["meta"]["scripts"] == []


def test_inline_body_crlf_and_long(session):
    cmd = "python3 - <<'EOF'\r\n" + "x=1\r\n" * 5000 + "EOF"
    [r] = evidence.handle("PreToolUse", bash(cmd, session), hash_of=versions.blob_hash)
    assert len(r["text"]) == 2000
    data = Path(r["file"]).read_bytes()
    assert data.count(b"x=1\r\n") == 5000


def test_bash_records_skill_script_hash(session, tmp_path):
    sk = tmp_path / "plug" / "skills" / "demo"
    (sk / "scripts").mkdir(parents=True)
    (sk / "scripts" / "run.py").write_text("print(1)\n")
    evidence.set_skill(paths.harness_dir(session), "demo", str(sk), 0)
    other = tmp_path / "other.py"
    other.write_text("x")
    cmd = f"python3 {sk}/scripts/run.py {other} {sk}/scripts/missing.py"
    [r] = evidence.handle("PreToolUse", bash(cmd, session), hash_of=versions.blob_hash)
    real = os.path.realpath(sk / "scripts" / "run.py")
    assert r["meta"]["scripts"] == [{"path": real, "hash": versions.blob_hash(real)}]
    assert evidence.skill_scripts(cmd, None) == []


def test_failure_record_and_interrupt_skipped(session):
    err = "\n".join(f"line{i}" for i in range(30))
    d = payload("PostToolUseFailure", session, tool_name="Bash", tool_input={"command": "x" * 900}, error=err)
    [r] = evidence.handle("PostToolUseFailure", d, hash_of=versions.blob_hash)
    assert r["kind"] == "failure"
    assert r["text"] == "Bash\n" + "\n".join(f"line{i}" for i in range(20))
    assert len(r["meta"]["input"]) == 600
    d["is_interrupt"] = True
    assert evidence.handle("PostToolUseFailure", d, hash_of=versions.blob_hash) == []


def _ask(session, **kw):
    qs = [{"question": "어느 쪽?", "options": [{"label": "A"}, {"label": "B"}]}]
    return payload("PostToolUse", session, tool_name="AskUserQuestion", tool_input={"questions": qs}, **kw)


def test_ask_record_text(session):
    d = _ask(session, tool_response={"answers": {"어느 쪽?": "B"}})
    [r] = evidence.handle("PostToolUse", d, hash_of=versions.blob_hash)
    assert r["kind"] == "ask"
    assert r["text"] == "Q: 어느 쪽?\n- A\n- B\n답: B"
    assert "answer_missing" not in r["meta"]
    d = _ask(session)
    d["tool_input"]["answers"] = {"어느 쪽?": "A"}
    [r] = evidence.handle("PostToolUse", d, hash_of=versions.blob_hash)
    assert r["text"].endswith("답: A")


def test_ask_without_answer_marks_missing(session):
    [r] = evidence.handle("PostToolUse", _ask(session, tool_response="whatever"), hash_of=versions.blob_hash)
    assert r["meta"]["answer_missing"] is True
    assert r["text"] == "Q: 어느 쪽?\n- A\n- B"


def test_skill_edit_for_skill_and_guideline_files_only(session, tmp_path):
    def edit(tool, rel):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("v")
        d = payload("PostToolUse", session, tool_name=tool, tool_input={"file_path": str(f)})
        return f, evidence.handle("PostToolUse", d, hash_of=versions.blob_hash)

    for tool, rel in [("Edit", "x/skills/s/SKILL.md"), ("Write", "r/CLAUDE.md"), ("MultiEdit", "r/.claude/rules/x.md"),
                      ("Edit", "r/.claude/CLAUDE.md"), ("Edit", "p/skills/s/scripts/a.py"), ("Edit", "q/SKILL.md"),
                      ("Edit", "repo/skills/x/scripts/run.py")]:
        if "skills/" in rel and not rel.endswith("SKILL.md"):
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel.split("/scripts")[0] / "SKILL.md").write_text("s")
        f, [r] = edit(tool, rel)
        assert r["kind"] == "skill_edit" and r["file"] == str(f)
        assert r["meta"]["hash_after"] == versions.blob_hash(f)
    for rel in ("skills/repo/src/a.py", "src/a.py", "r/.claude/rules/x.txt", "r/NOTCLAUDE.md"):
        assert edit("Edit", rel)[1] == []
    d = payload("PostToolUse", session, tool_name="Read", tool_input={"file_path": str(tmp_path / "r/CLAUDE.md")})
    assert evidence.handle("PostToolUse", d, hash_of=versions.blob_hash) == []


def test_skill_scripts_tilde_and_relative(tmp_path, monkeypatch):
    home = Path(os.path.expanduser("~"))
    sk = home / "skills" / "demo"
    (sk / "scripts").mkdir(parents=True)
    f = sk / "scripts" / "run.py"
    f.write_text("1")
    real = os.path.realpath(f)
    assert evidence.skill_scripts("python3 ~/skills/demo/scripts/run.py", str(sk)) == [real]
    assert evidence.skill_scripts("python3 scripts/run.py", str(sk), str(sk)) == [real]
    assert evidence.skill_scripts("python3 scripts/run.py", str(sk), str(tmp_path)) == []
    heredoc = f"python3 - <<'EOF'\n{f}\nEOF"
    assert evidence.skill_scripts(heredoc, str(sk)) == []
