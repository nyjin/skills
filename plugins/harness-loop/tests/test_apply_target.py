import os
import subprocess

import pytest

import apply_target


def _real(p):
    return os.path.realpath(p)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / "CLAUDE.md").write_text("rules")
    return r


def test_symlink_resolves_to_owner_repo(repo, tmp_path):
    link = tmp_path / "link.md"
    link.symlink_to(repo / "CLAUDE.md")
    c = apply_target.classify(str(link))
    assert c["kind"] == "repo"
    assert c["root"] == _real(repo)
    assert c["real_path"] == _real(repo / "CLAUDE.md")


def test_repo_rules_listed(repo):
    f = repo / "a.md"
    f.write_text("x")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "CLAUDE.md").write_text("y")
    c = apply_target.classify(str(f))
    root = _real(repo)
    assert c["rules"] == [f"{root}/CLAUDE.md", f"{root}/.claude/CLAUDE.md"]


def test_outside_git_is_local(tmp_path):
    f = tmp_path / "loose" / "n.md"
    f.parent.mkdir()
    f.write_text("x")
    c = apply_target.classify(str(f))
    assert c == {"kind": "local", "real_path": _real(f), "root": None, "rules": []}


def test_plugin_cache_is_not_editable(tmp_path):
    for sub in ("cache", "marketplaces"):
        f = tmp_path / "home" / ".claude" / "plugins" / sub / "p" / "SKILL.md"
        f.parent.mkdir(parents=True)
        f.write_text("x")
        assert apply_target.classify(str(f))["kind"] == "plugin_cache"


def test_tilde_expanded(tmp_path):
    f = tmp_path / "home" / ".claude" / "plugins" / "cache" / "SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    assert apply_target.classify("~/.claude/plugins/cache/SKILL.md")["kind"] == "plugin_cache"


def test_missing_file_is_new_with_parent_repo(repo):
    f = repo / "sub" / "deep" / "new.md"
    c = apply_target.classify(str(f))
    assert c["kind"] == "new"
    assert c["root"] == _real(repo)
    assert c["rules"] == [f"{_real(repo)}/CLAUDE.md"]
    assert c["real_path"] == _real(repo) + "/sub/deep/new.md"


def test_missing_file_outside_git_is_new_without_root(tmp_path):
    c = apply_target.classify(str(tmp_path / "nope" / "n.md"))
    assert c["kind"] == "new" and c["root"] is None and c["rules"] == []
