import importlib.util
import json
import sys
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
