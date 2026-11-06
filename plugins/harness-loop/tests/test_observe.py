import json

import pytest

import observe


def line(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


def asst(text, tools=(), mid="m1"):
    c = [{"type": "text", "text": text}] + [{"type": "tool_use", "name": n, "id": f"t-{n}"} for n in tools]
    return {"type": "assistant", "message": {"id": mid, "content": c}}


def make_session(root, project, sid, cwd, specs, transcript_lines=()):
    tdir = root / project
    tdir.mkdir(parents=True, exist_ok=True)
    tpath = tdir / f"{sid}.jsonl"
    tpath.write_text("".join(line(x) for x in transcript_lines), encoding="utf-8")
    hdir = tdir / sid / "harness"
    hdir.mkdir(parents=True)
    recs = []
    for n, (kind, at, extra) in enumerate(specs, 1):
        recs.append({"id": f"{sid[:8]}-{n}", "kind": kind, "at": at, "session": sid, "transcript": str(tpath),
                     "pos": tpath.stat().st_size, "cwd": cwd, "skill": None, "text": f"{kind}{n}", "file": None,
                     "flags": {}, "meta": {}, **extra})
    (hdir / "evidence.jsonl").write_text("".join(line(r) for r in recs), encoding="utf-8")
    return hdir, recs


@pytest.fixture
def two_sessions(tmp_projects):
    ha, ra = make_session(tmp_projects, "pa", "aaaaaaaa1", "/w/org/proj-a", [
        ("prompt", "2026-11-01T10:00:00+09:00", {}),
        ("skill_start", "2026-11-01T10:01:00+09:00", {}),
        ("bash", "2026-11-01T10:05:00+09:00", {}),
        ("skill_edit", "2026-11-01T10:06:00+09:00", {}),
    ])
    hb, rb = make_session(tmp_projects, "pb", "bbbbbbbb2", "/w/org/proj-b", [
        ("prompt", "2026-11-01T10:02:00+09:00", {}),
        ("failure", "2026-11-01T10:03:00+09:00", {}),
    ])
    return ha, ra, hb, rb


def test_excerpt_prompt_has_previous_assistant_tail(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(line(asst("앞" * 2000 + "끝", ["Bash", "Edit"])) + line({"type": "user", "message": {"content": "x"}}), encoding="utf-8")
    out = observe.excerpt(t, t.stat().st_size, "prompt")
    assert out.startswith("앞") and out.split("\n")[0].endswith("끝") and len(out.split("\n")[0]) == 1500
    assert "Bash, Edit" in out
    assert observe.excerpt(t, t.stat().st_size, "failure") == ""


def test_excerpt_skips_partial_line(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(line(asst("정상")) + "{깨진 줄\n" + '{"type":"assistant","message":{"con', encoding="utf-8")
    assert "정상" in observe.excerpt(t, t.stat().st_size, "prompt")


def test_excerpt_window_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(observe, "WINDOW", 200)
    t = tmp_path / "t.jsonl"
    t.write_text(line(asst("멀다")) + line({"type": "user", "message": {"content": "p" * 500}}), encoding="utf-8")
    assert observe.excerpt(t, t.stat().st_size, "prompt") == ""


def test_excerpt_ask_adds_tool_result(tmp_path, two_sessions):
    t = tmp_path / "t.jsonl"
    use = {"type": "assistant", "message": {"id": "m2", "content": [{"type": "tool_use", "name": "AskUserQuestion", "id": "q1"}]}}
    res = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "q1", "content": "예"}]}}
    t.write_text(line(asst("물어봄")) + line(use) + line(res), encoding="utf-8")
    assert "물어봄" in observe.excerpt(t, t.stat().st_size, "ask")
    hdir, recs = two_sessions[0], two_sessions[1]
    rec = {**recs[0], "kind": "ask", "transcript": str(t), "pos": t.stat().st_size, "meta": {"answer_missing": True}}
    (hdir / "evidence.jsonl").write_text(line(rec))
    got = observe.pending(10)
    assert any("응답: 예" in g["excerpt"] for g in got if g["id"] == rec["id"])


def test_pending_orders_across_sessions_and_skips_meta_kinds(two_sessions):
    got = observe.pending(10)
    assert [g["id"] for g in got] == ["aaaaaaaa-1", "bbbbbbbb-1", "bbbbbbbb-2", "aaaaaaaa-3"]
    assert got[0]["project"] == "org/proj-a" and "excerpt" in got[0]
    assert [g["id"] for g in observe.pending(2)] == ["aaaaaaaa-1", "bbbbbbbb-1"]


def test_save_validates_schema(two_sessions):
    with pytest.raises(ValueError, match="verdict"):
        observe.save({"observations": [{"evidence_id": "x", "type": "correction", "reason": "r"}]})
    with pytest.raises(ValueError, match="purpose"):
        observe.save({"observations": [{"evidence_id": "aaaaaaaa-3", "type": "repeat", "verdict": True, "reason": "r"}]})
    with pytest.raises(ValueError, match="evidence_id"):
        observe.save({"observations": [{"evidence_id": "nope", "type": "correction", "verdict": True, "reason": "r"}]})
    assert observe.observations() == []


def test_validate_subset():
    s = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}, "b": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 2}}}}
    observe.validate({"a": 1, "b": ["xx"]}, s)
    for bad, msg in (({"a": True}, r"\$\.a"), ({"b": ["xx"]}, "a"), ({"a": 1, "b": []}, r"\$\.b"), ({"a": 1, "b": ["x"]}, r"b\[0\]")):
        with pytest.raises(ValueError, match=msg):
            observe.validate(bad, s)


def test_observe_is_idempotent_per_evidence(two_sessions):
    eid = "bbbbbbbb-2"
    result = {"observations": [{"evidence_id": eid, "type": "failure", "verdict": True, "reason": "되돌림"}]}
    assert observe.save(result) == {"saved": 1, "skipped": 0}
    assert observe.save(result) == {"saved": 0, "skipped": 1}
    (o,) = observe.observations()
    assert o["session"] == "bbbbbbbb2" and o["project"] == "org/proj-b" and o["text"] == "failure2" and o["evidence_id"] == eid
    assert observe.observations(since="2026-11-02T00:00:00+09:00") == []


def test_multiple_observations_per_evidence(two_sessions):
    r = [{"evidence_id": "aaaaaaaa-1", "type": t, "verdict": False, "reason": "r"} for t in ("correction", "approval_question")]
    assert observe.save({"observations": r}) == {"saved": 2, "skipped": 0}


def test_cursor_advances_only_through_judged_evidence(two_sessions):
    ha, _, hb, _ = two_sessions
    observe.save({"observations": [{"evidence_id": "aaaaaaaa-1", "type": "correction", "verdict": False, "reason": "r"}]})
    assert (ha / "evidence.cursor").read_text() == "2"
    assert not (hb / "evidence.cursor").exists()
    assert [g["id"] for g in observe.pending(10)] == ["bbbbbbbb-1", "bbbbbbbb-2", "aaaaaaaa-3"]
    observe.save({"observations": [{"evidence_id": "aaaaaaaa-3", "type": "repeat", "verdict": True, "reason": "r", "purpose": "p"}]})
    assert (ha / "evidence.cursor").read_text() == "4"
    observe.save({"observations": [{"evidence_id": "aaaaaaaa-1", "type": "failure", "verdict": False, "reason": "r"}]})
    assert (ha / "evidence.cursor").read_text() == "4"
