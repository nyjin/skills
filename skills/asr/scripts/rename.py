"""전사본(.json)의 화자 이름을 바꾸고 같은 이름의 .md 를 다시 만든다.

    python rename.py <json> --set "1=김철수" --set "3=박지민"

결과 JSON 은 stdout 으로 낸다. 종료 코드: 성공 0, 입력 오류 2.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asrlib import transcript  # noqa: E402


def rename(json_path: Path, mapping: dict[int, str]) -> dict:
    json_path = Path(json_path)
    doc = transcript.load(json_path)
    by_label = {e["label_no"]: e for e in doc["speakers"].values() if "merged_into" not in e}
    for n in mapping:
        if n not in by_label:
            raise ValueError(f"화자 {n} 이 없습니다")
    for n, name in mapping.items():
        by_label[n]["name"] = name
    md_path = json_path.with_suffix(".md")
    transcript.save(doc, json_path, md_path)
    return {
        "md": str(md_path),
        "speakers": [{"label_no": n, "name": by_label[n].get("name")} for n in sorted(by_label)],
    }


def _parse_set(value: str) -> tuple[int, str]:
    num, sep, name = value.partition("=")
    name = name.strip()
    if not sep or not name or not num.strip().isdigit():
        raise ValueError(f'--set 형식이 올바르지 않습니다: "{value}" (예: --set "1=김철수")')
    return int(num), name


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="전사본의 화자 이름을 바꾼다.")
    parser.add_argument("json")
    parser.add_argument("--set", action="append", default=[], dest="sets", metavar="N=이름")
    args = parser.parse_args(argv)

    try:
        result = rename(Path(args.json), dict(_parse_set(v) for v in args.sets))
    except (ValueError, OSError) as e:
        print(e, file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
