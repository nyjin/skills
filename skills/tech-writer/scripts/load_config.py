#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""유저·프로젝트 config를 병합하고 검증해 JSON으로 출력한다.

종료 코드: 0 정상, 2 오류, 3 config 없음(인터뷰 필요).
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

DEFAULTS = Path(__file__).resolve().parents[1] / "config" / "default.yaml"
ENUMS = {
    ("style", "tone"): {"합니다체", "해요체", "개조식"},
    ("style", "loanword_policy"): {"usage", "standard"},
    ("output", "format"): {"markdown", "confluence", "html"},
    ("output", "diagrams"): {"auto", "mermaid", "archify", "none"},
}
LEVELS = {"beginner", "intermediate", "expert", "non-technical"}


class ConfigError(Exception):
    def __init__(self, file, line, message):
        super().__init__(message)
        self.file, self.line, self.message = file, line, message


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise ConfigError(str(path), (mark.line + 1) if mark else 0, f"YAML 문법 오류: {e}")
    if not isinstance(data, dict):
        raise ConfigError(str(path), 1, "최상위는 키-값 매핑이어야 한다.")
    return data


def find_project_config(start: Path) -> Path | None:
    home = Path.home().resolve()
    cur = start.resolve()
    while True:
        candidate = cur / ".tech-writing.yaml"
        if candidate.is_file():
            return candidate
        if (cur / ".git").exists() or cur == home or cur.parent == cur:
            return None
        cur = cur.parent


def validate(config: dict) -> list[str]:
    errors = []
    aud = config.get("audiences") or {}
    profiles = aud.get("profiles") or {}
    default = aud.get("default")
    if default is not None and default not in profiles:
        errors.append(f"audiences.default '{default}'가 profiles에 없다.")
    for name, prof in profiles.items():
        level = (prof or {}).get("level")
        if level not in LEVELS:
            errors.append(f"audiences.profiles.{name}.level '{level}'은 {sorted(LEVELS)} 중 하나여야 한다.")
    for (section, key), allowed in ENUMS.items():
        value = (config.get(section) or {}).get(key)
        if value not in allowed:
            errors.append(f"{section}.{key} '{value}'은 {sorted(allowed)} 중 하나여야 한다.")
    return errors


def unknown_keys(config: dict, defaults: dict, prefix="") -> list[str]:
    found = []
    for key, value in config.items():
        path = f"{prefix}{key}"
        if key not in defaults:
            found.append(path)
        elif isinstance(value, dict) and isinstance(defaults[key], dict) and defaults[key]:
            found += unknown_keys(value, defaults[key], path + ".")
    return found


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--user", default=str(Path.home() / ".claude/tech-writing/config.yaml"))
    p.add_argument("--project-dir", default=".")
    args = p.parse_args(argv)

    user = Path(args.user).expanduser()
    project = find_project_config(Path(args.project_dir))
    present = [x for x in (user, project) if x and x.is_file()]
    if not present:
        print(json.dumps({"status": "missing"}, ensure_ascii=False))
        return 3
    try:
        defaults = read_yaml(DEFAULTS)
        merged = defaults
        warnings = []
        for path in present:
            data = read_yaml(path)
            warnings += [f"{path}: 알 수 없는 키 '{k}'" for k in unknown_keys(data, defaults)]
            merged = deep_merge(merged, data)
    except ConfigError as e:
        print(json.dumps({"status": "error", "errors": [{"file": e.file, "line": e.line, "message": e.message}]}, ensure_ascii=False))
        return 2
    errors = validate(merged)
    if errors:
        print(json.dumps({"status": "error", "errors": [{"file": str(present[-1]), "line": 0, "message": m} for m in errors]}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "ok", "config": merged, "sources": [str(x) for x in present], "warnings": warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
