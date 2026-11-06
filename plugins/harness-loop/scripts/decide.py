#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""결정 기록(decisions.jsonl)과 그 라벨·정밀도, replay 보관, 티켓 근거 파일."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import apply_target
import groups
import observe
import paths
import versions

CHOICES = ("proceed", "ticket", "reject")
KINDS = ("not-defect", "trivial", "wont-fix")
CONTEXT_LINES = 15


def _file() -> Path:
    return paths.data_dir() / "decisions.jsonl"


def _rubric() -> tuple[str, int]:
    text = (paths.data_dir() / "rubric.md").read_text(encoding="utf-8")
    m = re.match(r"<!--\s*rubric-version:\s*(\d+)\s*-->", text)
    return text, int(m.group(1)) if m else 1


def _target(group: dict) -> str:
    return os.path.expanduser(group["target_path"])


def record(group_id: str, choice: str, *, kind=None, reason=None, baseline_hash=None, ticket=None) -> dict:
    if choice not in CHOICES:
        raise ValueError(f"choice 는 {'·'.join(CHOICES)} 중 하나여야 합니다: {choice}")
    if choice == "reject" and kind not in KINDS:
        raise ValueError(f"reject 는 kind({'·'.join(KINDS)})가 필요합니다")
    group = groups.load().get(group_id)
    if group is None:
        raise ValueError(f"알 수 없는 묶음: {group_id}")
    if choice == "proceed":
        if apply_target.classify(group["target_path"])["kind"] == "plugin_cache":
            raise ValueError("외부 플러그인 캐시 대상은 진행할 수 없습니다. 원본 저장소에 티켓을 내세요")
        baseline_hash = baseline_hash or versions.blob_hash(_target(group))
    rec = {"group": group_id, "decision": choice, "kind": kind, "note": reason,
           "baseline_hash": baseline_hash, "ticket": ticket, "at": paths.now(),
           "rubric_version": _rubric()[1]}
    paths.append_line(_file(), rec)
    archive(group_id)
    return rec


def last_decisions() -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        lines = _file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("group"):
            out[rec["group"]] = rec
    return out


def _label(rec: dict) -> str:
    if rec["decision"] in ("proceed", "ticket"):
        return "positive"
    return "excluded" if rec.get("kind") == "wont-fix" else "negative"


def labels() -> dict[str, dict]:
    return {g: {"label": _label(r), "kind": r.get("kind"), "note": r.get("note"), "at": r.get("at")}
            for g, r in last_decisions().items()}


def split(group_id: str) -> str:
    return "test" if hashlib.sha1(group_id.encode("utf-8")).digest()[0] < 128 else "train"


def precision() -> dict:
    ls = [v["label"] for v in labels().values()]
    pos, neg = ls.count("positive"), ls.count("negative")
    return {"positive": pos, "negative": neg, "precision": pos / (pos + neg) if pos + neg else None}


def _evidence() -> dict[str, dict]:
    out = {}
    for hdir in paths.all_harness_dirs():
        try:
            lines = (hdir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("id"):
                out[r["id"]] = r
    return out


def _excerpt(ev: dict | None) -> str:
    return observe.excerpt(ev.get("transcript", ""), ev.get("pos") or 0, ev["kind"]) if ev else ""


def _version_text(target: str, h: str) -> str | None:
    root = versions.git_root(target)
    if root:
        try:
            r = subprocess.run(["git", "-C", root, "cat-file", "-p", h], capture_output=True, timeout=5)
            if r.returncode == 0:
                return r.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return (paths.data_dir() / "skill-snapshots" / h).read_bytes().decode("utf-8", "replace")
    except OSError:
        return None


def archive(group_id: str) -> Path:
    group = groups.load()[group_id]
    by_obs = {o.get("evidence_id"): o for o in observe.observations()}
    evidence = _evidence()
    target = _target(group)
    entries, hashes = [], []
    for eid in group["observations"]:
        obs, ev = by_obs.get(eid), evidence.get(eid)
        entries.append({"evidence_id": eid, "observation": obs, "evidence": ev, "excerpt": _excerpt(ev)})
        h = groups.obs_hash(obs, target) if obs else None
        if h and h not in hashes:
            hashes.append(h)
    files = {h: t for h in hashes if (t := _version_text(target, h)) is not None}
    text, version = _rubric()
    clean = {k: v for k, v in group.items() if k not in ("decision", "ticket", "baseline_hash")}
    out = paths.data_dir() / "replay" / group_id / "input.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    paths.atomic_write(out, json.dumps(
        {"group": clean, "observations": entries, "file_versions": files,
         "rubric": {"version": version, "text": text}}, ensure_ascii=False, indent=2))
    return out


def _read(path) -> str | None:
    try:
        return Path(os.path.expanduser(str(path))).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _around(target: str, line: int) -> str | None:
    text = _read(target)
    if text is None:
        return None
    lo = max(line - 1 - CONTEXT_LINES, 0)
    return "\n".join(f"{i + 1}: {s}" for i, s in enumerate(text.splitlines()[lo:line + CONTEXT_LINES], lo))


def ticket_file(group_id: str, out_dir) -> Path:
    group = groups.load()[group_id]
    by_obs = {o.get("evidence_id"): o for o in observe.observations()}
    evidence = _evidence()
    tp = group["target_path"]
    name = Path(tp).parent.name if tp.endswith("SKILL.md") else Path(tp).name
    parts = [f"# [결정 대상] {name} — {group['problem']}", ""]
    scripts, prompts, reasons = [], [], []
    for eid in group["observations"]:
        obs = by_obs.get(eid) or {}
        body = _read(obs["file"]) if obs.get("file") and obs.get("type") == "repeat" else None
        if body is not None and body not in scripts:
            scripts.append(body)
        if obs.get("type") == "correction":
            prompts.append((obs.get("text") or "", _excerpt(evidence.get(eid))))
        if obs.get("reason"):
            reasons.append(obs["reason"])
    if scripts:
        parts += ["## 반복 스크립트 원문", ""] + [f"```\n{s}\n```\n" for s in scripts]
    if prompts:
        parts += ["## 교정 프롬프트", ""]
        for text, ex in prompts:
            parts += [f"> {text}", ""] + ([f"직전 응답 발췌:\n\n```\n{ex}\n```", ""] if ex else [])
    line = (group.get("verified") or {}).get("line")
    if line:
        seg = _around(_target(group), line)
        if seg:
            parts += [f"## 대상 파일 {line}줄 부근", "", f"```\n{seg}\n```", ""]
    prop = group.get("proposal") or {}
    parts += ["## 수정안", "", f"범위: {prop.get('scope', '')}", "", f"의견: {prop.get('opinion', '')}", "",
              f"```diff\n{prop.get('diff', '')}\n```", ""]
    if reasons:
        parts += ["## 판정 이유", ""] + [f"- {r}" for r in reasons] + [""]
    out = Path(out_dir) / f"{group_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out
