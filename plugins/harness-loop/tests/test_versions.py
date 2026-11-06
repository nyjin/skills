import hashlib
import os
import subprocess
from pathlib import Path

import pytest

import paths
import versions


def git(repo, *args, env=None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                       env={**os.environ, **(env or {})})
    return r.stdout


def commit(repo, msg, when):
    git(repo, "commit", "-qam", msg, env={"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})


def blob(text):
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "skills/a").mkdir(parents=True)
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "skills/a/SKILL.md").write_text("v1\n")
    git(r, "add", ".")
    commit(r, "init", "2026-01-01T00:00:00+00:00")
    return r


def test_blob_hash_matches_git(repo):
    f = repo / "skills/a/SKILL.md"
    assert versions.blob_hash(f) == git(repo, "hash-object", str(f)).strip()


def test_blob_hash_missing_file_is_none(tmp_path):
    assert versions.blob_hash(tmp_path / "없음.md") is None


def test_committed_version_not_snapshotted(repo):
    h = versions.remember(repo / "skills/a/SKILL.md")
    assert versions.commit_of(repo / "skills/a/SKILL.md", h)["subject"] == "init"
    assert not (paths.data_dir() / "skill-snapshots" / h).exists()


def test_uncommitted_version_snapshotted_then_found_after_commit(repo):
    f = repo / "skills/a/SKILL.md"
    f.write_text("v2\n")
    h = versions.remember(f)
    assert (paths.data_dir() / "skill-snapshots" / h).read_text() == "v2\n"
    git(repo, "commit", "-qam", "v2")
    assert versions.commit_of(f, h)["subject"] == "v2"


def test_staged_but_uncommitted_is_snapshotted(repo):
    f = repo / "skills/a/SKILL.md"
    f.write_text("staged\n")
    git(repo, "add", ".")
    h = versions.remember(f)
    assert (paths.data_dir() / "skill-snapshots" / h).exists()


def test_history_merges_commits_and_snapshots_in_time_order(repo):
    f = repo / "skills/a/SKILL.md"
    snaps = paths.data_dir() / "skill-snapshots"
    f.write_text("v2\n")
    h2 = versions.remember(f)
    os.utime(snaps / h2, (1767229200, 1767229200))  # 2026-01-01T01:00Z
    commit(repo, "v2", "2026-01-01T02:00:00+00:00")
    f.write_text("v3\n")
    h3 = versions.remember(f)
    os.utime(snaps / h3, (1767240000, 1767240000))  # 2026-01-01T04:00Z
    hist = versions.history(f, "2025-12-31T00:00:00+00:00")
    assert [(e["hash"], e["source"]) for e in hist] == [
        (blob("v1\n"), "commit"), (h2, "snapshot"), (h3, "snapshot")]
    assert hist[0]["subject"] == "init" and "commit" in hist[0]


def test_hash_at(repo):
    f = repo / "skills/a/SKILL.md"
    f.write_text("v2\n")
    commit(repo, "v2", "2026-01-02T00:00:00+00:00")
    assert versions.hash_at(f, "2026-01-01T12:00:00+00:00") == blob("v1\n")
    assert versions.hash_at(f, "2026-01-03T00:00:00+00:00") == blob("v2\n")


def test_outside_git_snapshots_and_notice(tmp_path):
    f = tmp_path / "skills/b/SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    assert versions.git_root(f) is None
    assert versions.hash_at(f, "2026-01-01T00:00:00+00:00") is None
    assert (paths.data_dir() / "skill-snapshots" / versions.remember(f)).exists()
    [line] = versions.untracked_notice({str(tmp_path / "skills"): ["b"]})
    assert line.startswith("버전 추적 불가: ") and line.endswith("해당 스킬: b")


def test_plugin_cache_notice_uses_ticket_text():
    root = Path.home() / ".claude/plugins/cache/x/skills"
    root.mkdir(parents=True)
    assert versions.in_plugin_cache(root / "a" / "SKILL.md")
    assert versions.untracked_notice({str(root): ["a"]}) == [f"원본 저장소에 티켓을 내세요: {root}"]


def test_remember_never_raises(tmp_path):
    assert versions.remember(tmp_path / "없음") is None
