#!/usr/bin/env bash
# check_env.sh 테스트. 덮어쓰기 환경변수로 각 경우를 만들어 출력과 종료 코드를 단언한다.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/check_env.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
OUT=""
CODE=0

run() { # run [ENV=VAL ...] — 기본값은 지원 환경(Darwin, arm64, 48GB), 빈 hub
  OUT="$(env ASR_UNAME_S=Darwin ASR_ARM64=1 ASR_MEM_BYTES=51539607552 \
    ASR_HF_HUB="$TMP/empty-hub" ASR_VENV="$TMP/no-venv" "$@" bash "$SCRIPT" 2>&1)"
  CODE=$?
}

check() { # check <이름> <조건 결과 0/1>
  if [ "$2" -eq 0 ]; then
    PASS=$((PASS + 1)); echo "ok   - $1"
  else
    FAIL=$((FAIL + 1)); echo "FAIL - $1"; echo "$OUT" | sed 's/^/       /'; echo "       exit=$CODE"
  fi
}

has() { printf '%s\n' "$OUT" | grep -qxF -- "$1"; }
has_re() { printf '%s\n' "$OUT" | grep -qE -- "$1"; }

mkdir -p "$TMP/empty-hub"

# 1. linux is unsupported
run ASR_UNAME_S=Linux
has STATUS=unsupported && has_re '^UNSUPPORTED_REASON=.+' && [ "$CODE" -eq 20 ]
check "linux is unsupported" $?

# 2. intel is unsupported
run ASR_ARM64=0
has STATUS=unsupported && [ "$CODE" -eq 20 ]
check "intel is unsupported" $?

# 3. low memory is unsupported
run ASR_MEM_BYTES=17179869184
has STATUS=unsupported && has_re '^UNSUPPORTED_REASON=.*16GB' && [ "$CODE" -eq 20 ]
check "low memory is unsupported" $?

# 4. empty hub is missing
run
has MODEL_VIBEVOICE=missing && has STATUS=missing && [ "$CODE" -eq 10 ]
check "empty hub is missing" $?

# 가짜 모델 캐시 만들기
HUB="$TMP/hub"
make_model() { # make_model <org--name> <필수 파일...>
  local d="$HUB/models--$1" f
  shift
  mkdir -p "$d/blobs" "$d/snapshots/abc123"
  for f in "$@"; do
    echo data > "$d/blobs/blob-$f"
    ln -s "../../blobs/blob-$f" "$d/snapshots/abc123/$f"
  done
}
make_model mlx-community--VibeVoice-ASR-8bit config.json model-00001.safetensors
make_model mlx-community--silero-vad model.safetensors
make_model aufklarer--SpeechBrain-ECAPA-VoxCeleb-20M-MLX model.safetensors

# 5. incomplete model is missing
touch "$HUB/models--mlx-community--silero-vad/blobs/x.incomplete"
run ASR_HF_HUB="$HUB"
has MODEL_VAD=missing && has MODEL_VIBEVOICE=present
check "incomplete model is missing" $?
rm "$HUB/models--mlx-community--silero-vad/blobs/x.incomplete"

# 6. all present is ready — ffmpeg·python 은 가짜 bin 으로 고정해 호스트에 기대지 않는다
BIN="$TMP/bin"
mkdir -p "$BIN" "$TMP/venv/bin"
printf '#!/bin/sh\nexit 0\n' > "$BIN/ffmpeg"
printf '#!/bin/sh\necho "Python 3.12.0"\n' > "$BIN/python3.12"
printf '#!/bin/sh\necho 0.6.5\n' > "$TMP/venv/bin/python"
chmod +x "$BIN/ffmpeg" "$BIN/python3.12" "$TMP/venv/bin/python"
run ASR_HF_HUB="$HUB" ASR_VENV="$TMP/venv" ASR_PATH_OVERRIDE="$BIN:/usr/bin:/bin"
has STATUS=ready && has MLX_AUDIO=0.6.5 && has VENV=present && has FFMPEG=present \
  && has "PYTHON=$BIN/python3.12" && [ "$CODE" -eq 0 ]
check "all present is ready" $?

# 7. old mlx-audio is missing
printf '#!/bin/sh\necho 0.5.0\n' > "$TMP/venv/bin/python"
run ASR_HF_HUB="$HUB" ASR_VENV="$TMP/venv" ASR_PATH_OVERRIDE="$BIN:/usr/bin:/bin"
has STATUS=missing && has MLX_AUDIO=0.5.0 && [ "$CODE" -eq 10 ]
check "old mlx-audio is missing" $?

# 8. mlx-audio that fails to import is absent
printf '#!/bin/sh\necho 0.6.5\nexit 1\n' > "$TMP/venv/bin/python"
run ASR_HF_HUB="$HUB" ASR_VENV="$TMP/venv" ASR_PATH_OVERRIDE="$BIN:/usr/bin:/bin"
has STATUS=missing && has MLX_AUDIO=absent && [ "$CODE" -eq 10 ]
check "mlx-audio import failure is absent" $?

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
