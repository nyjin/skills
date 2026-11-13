import json
import os
import time
from datetime import datetime

import yaml

import paths


def test_data_dir_copies_defaults_once(tmp_path):
    d = paths.data_dir()
    assert yaml.safe_load((d / "config.yaml").read_text())["replay_min_labels"] == 30
    assert (d / "rubric.md").exists()
    assert (d / "installed_at").exists()
    (d / "config.yaml").write_text("window_days: 7\n")
    paths.data_dir()
    assert paths.load_config()["window_days"] == 7
    assert paths.load_config()["pattern_min_count"] == 2


def test_defaults_file_matches_default_config():
    assert yaml.safe_load((paths.DEFAULTS / "config.yaml").read_text()) == paths.DEFAULT_CONFIG


def test_broken_config_falls_back_and_logs():
    (paths.data_dir() / "config.yaml").write_text("[1,")
    assert paths.load_config() == paths.DEFAULT_CONFIG
    assert "config.yaml" in (paths.data_dir() / "hook-errors.log").read_text()


def test_non_mapping_config_falls_back_and_logs():
    (paths.data_dir() / "config.yaml").write_text("- 1\n- 2\n")
    assert paths.load_config() == paths.DEFAULT_CONFIG
    assert "config.yaml" in (paths.data_dir() / "hook-errors.log").read_text()


def test_harness_paths(tmp_projects):
    t = tmp_projects / "proj" / "sess1.jsonl"
    p = paths.harness_path(t)
    assert p == tmp_projects / "proj" / "sess1" / "harness"
    assert not p.exists()
    assert paths.harness_dir(t) == p and p.is_dir()
    assert paths.all_harness_dirs() == [p]


def test_now_and_aware():
    assert paths.aware(paths.now()).tzinfo is not None
    assert paths.aware("2026-01-01T00:00:00").tzinfo is not None
    assert isinstance(paths.aware("2026-01-01T00:00:00+00:00"), datetime)


def test_atomic_write(tmp_path):
    p = tmp_path / "f.txt"
    paths.atomic_write(p, "한글")
    paths.atomic_write(p, "둘")
    assert p.read_text(encoding="utf-8") == "둘"
    assert [x.name for x in tmp_path.iterdir() if x.name.startswith("f.txt")] == ["f.txt"]


def test_append_line_writes_one_json_per_line(tmp_path):
    p = tmp_path / "e.jsonl"
    paths.append_line(p, {"a": "줄\n바꿈"})
    paths.append_line(p, {"b": 2})
    assert [json.loads(l) for l in p.read_text().splitlines()] == [{"a": "줄\n바꿈"}, {"b": 2}]


def test_lock_is_exclusive_and_stale_lock_reclaimed(tmp_path):
    assert paths.LOCKED_EXIT == 3
    assert paths.acquire_lock(tmp_path) is True
    assert paths.acquire_lock(tmp_path) is False
    old = time.time() - 700
    os.utime(tmp_path / "lock", (old, old))
    assert paths.acquire_lock(tmp_path) is True
    paths.release_lock(tmp_path)
    assert not (tmp_path / "lock").exists()
    paths.release_lock(tmp_path)


def test_log_error_never_raises(monkeypatch):
    paths.log_error("hook", ValueError("나쁨\n줄"))
    lines = (paths.data_dir() / "hook-errors.log").read_text().splitlines()
    assert len(lines) == 1 and "hook" in lines[0] and "ValueError" in lines[0]

    def boom():
        raise OSError("x")

    monkeypatch.setattr(paths, "data_dir", boom)
    paths.log_error("hook", ValueError("y"))


def test_repo_roots_shared_by_subdir_and_worktree_and_plain_dir(tmp_path):
    import subprocess

    repo = tmp_path / "main"
    (repo / "sub").mkdir(parents=True)
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "f").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True, capture_output=True)
    assert paths.repo_roots(repo / "sub") == (str(repo.resolve()), str(repo.resolve()))
    assert paths.repo_roots(wt) == (str(wt.resolve()), str(repo.resolve()))
    plain = tmp_path / "plain"
    plain.mkdir()
    assert paths.repo_roots(plain) == (None, str(plain))
