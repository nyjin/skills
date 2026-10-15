#!/usr/bin/env bash
# mermaid 소스를 SVG/PNG로 렌더링한다.
# 사용: render-mermaid.sh <input.mmd> <output.svg|png>
# mmdc가 있으면 쓰고, 없으면 npx로 @mermaid-js/mermaid-cli를 실행한다.
set -uo pipefail

if [ $# -ne 2 ]; then
  echo "사용: render-mermaid.sh <input.mmd> <output.svg|png>" >&2
  exit 2
fi
in="$1"
out="$2"

if command -v mmdc >/dev/null 2>&1; then
  cmd=(mmdc)
else
  cmd=(npx -y @mermaid-js/mermaid-cli)
fi

rm -f "$out"
if ! log=$("${cmd[@]}" -i "$in" -o "$out" -b transparent 2>&1); then
  echo "mermaid 렌더링 실패: $in" >&2
  echo "$log" >&2
  exit 1
fi
if [ ! -s "$out" ]; then
  echo "mermaid 렌더링 결과가 비어 있다: $out" >&2
  echo "$log" >&2
  exit 1
fi
