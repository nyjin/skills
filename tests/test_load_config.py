import json
import subprocess
from pathlib import Path

from conftest import ROOT, SCRIPTS, load_script

lc = load_script("load_config")

USER = """\
audiences:
  default: backend-dev
  profiles:
    backend-dev: {role: 백엔드 개발자, level: intermediate}
style: {tone: 합니다체}
"""


def run(tmp_path, user=None, project=None):
    user_path = tmp_path / "user.yaml"
    if user is not None:
        user_path.write_text(user)
    proj = tmp_path / "repo"
    proj.mkdir(exist_ok=True)
    (proj / ".git").mkdir(exist_ok=True)
    if project is not None:
        (proj / ".tech-writing.yaml").write_text(project)
    proc = subprocess.run(
        ["uv", "run", str(SCRIPTS / "load_config.py"), "--user", str(user_path), "--project-dir", str(proj)],
        capture_output=True, text=True, cwd=ROOT,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_deep_merge_replaces_scalars_and_merges_dicts():
    assert lc.deep_merge({"a": {"x": 1, "y": 2}, "l": [1]}, {"a": {"y": 3}, "l": [2]}) == {"a": {"x": 1, "y": 3}, "l": [2]}


def test_missing_both_returns_status_missing(tmp_path):
    code, out = run(tmp_path)
    assert code == 3 and out["status"] == "missing"


def test_user_only_fills_defaults(tmp_path):
    code, out = run(tmp_path, user=USER)
    assert code == 0
    cfg = out["config"]
    assert cfg["style"]["loanword_policy"] == "usage"
    assert cfg["output"]["format"] == "markdown"
    assert cfg["style"]["tone"] == "합니다체"


def test_project_adds_profile_keeps_user_profiles(tmp_path):
    project = "audiences:\n  default: pm\n  profiles:\n    pm: {role: PM, level: non-technical}\n"
    code, out = run(tmp_path, user=USER, project=project)
    assert code == 0
    profiles = out["config"]["audiences"]["profiles"]
    assert set(profiles) == {"backend-dev", "pm"}
    assert out["config"]["audiences"]["default"] == "pm"
    assert len(out["sources"]) == 2


def test_malformed_yaml_reports_location(tmp_path):
    code, out = run(tmp_path, user="audiences:\n  default: [unclosed\n")
    assert code == 2
    err = out["errors"][0]
    assert err["file"].endswith("user.yaml") and err["line"] >= 1


def test_default_profile_must_exist(tmp_path):
    code, out = run(tmp_path, user="audiences:\n  default: ghost\n  profiles:\n    a: {role: x, level: expert}\n")
    assert code == 2
    assert "ghost" in out["errors"][0]["message"]


def test_invalid_enum_rejected(tmp_path):
    bad = USER.replace("intermediate", "guru")
    code, out = run(tmp_path, user=bad)
    assert code == 2
    assert "level" in out["errors"][0]["message"]


def test_unknown_key_is_warning_not_error(tmp_path):
    code, out = run(tmp_path, user=USER + "extra: 1\n")
    assert code == 0
    assert any("extra" in w for w in out["warnings"])


def test_project_search_stops_at_git_root(tmp_path):
    outer = tmp_path / ".tech-writing.yaml"
    outer.write_text("style: {tone: 해요체}\n")
    code, out = run(tmp_path, user=USER)
    assert out["config"]["style"]["tone"] == "합니다체"
