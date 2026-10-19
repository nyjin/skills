import os

import state


def test_data_dir_copies_defaults_once(tmp_projects):
    d = state.data_dir()
    assert (d / "scoring.yaml").exists() and (d / "installed_at").exists()
    (d / "scoring.yaml").write_text("propose: 9\n")
    state.data_dir()
    assert state.load_config()["propose"] == 9
    assert state.load_config()["queue_limit"] == 3


def test_lock_is_exclusive_and_stale_lock_reclaimed(tmp_path):
    assert state.acquire_lock(tmp_path) is True
    assert state.acquire_lock(tmp_path) is False
    os.utime(tmp_path / "lock", (0, 0))
    assert state.acquire_lock(tmp_path, stale_sec=600) is True
    state.release_lock(tmp_path)
    assert not (tmp_path / "lock").exists()


def test_cursor_and_items_roundtrip(tmp_path):
    assert state.read_cursor(tmp_path) == 0
    state.write_cursor(tmp_path, 42)
    assert state.read_cursor(tmp_path) == 42
    state.save_items(tmp_path, {"C1:x": {"sig": "C1:x", "status": "watch"}})
    assert state.load_items(tmp_path)["C1:x"]["status"] == "watch"


def test_decisions_last_wins(tmp_projects):
    state.append_decision("C1:x", "rejected")
    state.append_decision("C1:x", "approved", target="/a")
    d = state.load_decisions()["C1:x"]
    assert d["decision"] == "approved" and d["target"] == "/a" and d["at"]


def test_harness_dir_and_discovery(tmp_projects):
    t = tmp_projects / "proj" / "sess1.jsonl"
    t.parent.mkdir()
    t.write_text("")
    assert state.find_transcript("sess1") == t
    assert state.find_transcript("nope") is None
    h = state.harness_dir(t)
    assert h == tmp_projects / "proj" / "sess1" / "harness" and h.is_dir()
    assert state.all_harness_dirs() == [h]


def test_load_config_falls_back_on_broken_yaml(tmp_projects):
    d = state.data_dir()
    for text in ("weights: [\n", "- a\n- b\n"):
        (d / "scoring.yaml").write_text(text)
        assert state.load_config() == state.DEFAULT_CONFIG
    log = (d / "hook-errors.log").read_text().splitlines()
    assert len(log) == 2 and all("scoring.yaml" in line for line in log)


def test_load_decisions_skips_malformed_lines(tmp_projects):
    state.append_decision("C1:x", "approved")
    with open(state.data_dir() / "decisions.jsonl", "a") as f:
        f.write('[1, 2]\n"text"\n{"sig": "C1:y"}\n{"decision": "approved", "at": "2026-10-01T00:00:00+09:00"}\n')
    assert list(state.load_decisions()) == ["C1:x"]


def test_harness_dir_lives_under_projects_root(tmp_projects, tmp_path):
    t = tmp_path / "elsewhere" / "-src-app" / "sess9.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text("")
    h = state.harness_dir(t)
    assert h == tmp_projects / "-src-app" / "sess9" / "harness" and h.is_dir()
    assert not (t.parent / "sess9").exists()


def test_find_transcript_falls_back_to_default_projects(tmp_projects, tmp_path):
    default = tmp_path / "home" / ".claude" / "projects" / "-src-app" / "sess9.jsonl"
    default.parent.mkdir(parents=True)
    default.write_text("")
    assert state.find_transcript("sess9") == default
    mine = tmp_projects / "-src-app" / "sess9.jsonl"
    mine.parent.mkdir()
    mine.write_text("")
    assert state.find_transcript("sess9") == mine


def test_sig_key_folds_c7_into_c2():
    assert state.sig_key("C7:rule-scope") == "C2:rule-scope"
    assert state.sig_key("C2:rule-scope") == "C2:rule-scope"
    assert state.sig_key("C1:x") == "C1:x"
    assert state.sig_key("C17:x") == "C17:x"


def test_decisions_by_key_last_wins_across_c2_c7_twins(tmp_projects):
    state.append_decision("C2:x", "approved")
    state.append_decision("C7:x", "rejected")
    state.append_decision("C7:x", "edit-score", impact=2)
    d = state.load_decisions_by_key()["C2:x"]
    assert d["decision"] == "rejected" and d["sig"] == "C7:x"
    assert set(state.load_decisions()) == {"C2:x", "C7:x"}


def test_session_start_skips_malformed_timestamp(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text('{"timestamp": "not-a-date"}\n{"timestamp": "2026-10-01T09:00:00+09:00"}\n')
    assert state.session_start(t).isoformat() == "2026-10-01T09:00:00+09:00"
