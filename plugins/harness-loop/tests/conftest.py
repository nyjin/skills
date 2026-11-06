import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def tmp_projects(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("HARNESS_PROJECTS_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return root


@pytest.fixture
def obs_factory(tmp_projects):
    """근거 레코드를 세션 evidence.jsonl 에 쓰고 observe.save 로 관찰을 만든다. evidence_id 를 돌려준다."""
    import observe

    n = [0]

    def make(type_="correction", at="2026-11-05T10:00:00+09:00", session="s1", cwd="/w/org/proj-a",
             verdict=True, skill=None, meta=None):
        n[0] += 1
        eid = f"{session}-{n[0]}"
        hdir = tmp_projects / "p" / session / "harness"
        hdir.mkdir(parents=True, exist_ok=True)
        rec = {"id": eid, "kind": "prompt", "at": at, "session": session, "transcript": "", "pos": 0,
               "cwd": cwd, "skill": skill, "text": "t", "file": None, "flags": {}, "meta": meta or {}}
        with open(hdir / "evidence.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        entry = {"evidence_id": eid, "type": type_, "verdict": verdict, "reason": "이유"}
        if type_ == "repeat":
            entry["purpose"] = "같은 목적"
        observe.save({"observations": [entry]})
        return eid

    return make


REDESIGN = Path(__file__).parent / "fixtures" / "redesign"
SESSION_A = "aaaa1111-0000-0000-0000-000000000000"
SESSION_B = "bbbb2222-0000-0000-0000-000000000000"


def _git(repo, *args, when=None):
    env = {**os.environ, **({"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when} if when else {})}
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)


@pytest.fixture
def redesign_sessions(tmp_path, tmp_projects):
    """skills/a 스킬과 CLAUDE.md 를 날짜를 정해 커밋한 저장소와, 그 저장소를 cwd 로 쓰는 두 세션 기록(fixtures/redesign).
    SKILL.md: v1(10-01)→v2(10-20), CLAUDE.md: g1(10-01)→g2(10-28). 세션 a 는 10-10, b 는 10-25·10-30."""
    repo = tmp_path / "repo"
    (repo / "skills/a").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "skills/a/SKILL.md").write_text("v1\n")
    (repo / "skills/a/run.sh").write_text("echo run\n")
    (repo / "CLAUDE.md").write_text("g1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init", when="2026-10-01T00:00:00+00:00")
    (repo / "skills/a/SKILL.md").write_text("v2\n")
    _git(repo, "commit", "-qam", "skill v2", when="2026-10-20T00:00:00+00:00")
    (repo / "CLAUDE.md").write_text("g2\n")
    _git(repo, "commit", "-qam", "guide g2", when="2026-10-28T00:00:00+00:00")
    out = {"repo": repo}
    for key, project, sid in (("a", "-w-proj-a", SESSION_A), ("b", "-w-proj-b", SESSION_B)):
        text = (REDESIGN / f"session-{key}.jsonl").read_text(encoding="utf-8").replace("{REPO}", str(repo))
        dst = tmp_projects / project / f"{sid}.jsonl"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        ts = datetime.fromisoformat(json.loads(text.splitlines()[-1])["timestamp"]).timestamp()
        os.utime(dst, (ts, ts))
        out[key] = dst
    return out
