#!/usr/bin/env bash
# 환경 점검. stdout 에 KEY=VALUE 를 한 줄씩 내고 마지막 줄은 STATUS=ready|missing|unsupported.
# 종료 코드: ready 0 / missing 10 / unsupported 20.
# 테스트용 덮어쓰기: ASR_UNAME_S, ASR_ARM64(0/1), ASR_MEM_BYTES, ASR_HF_HUB, ASR_VENV, ASR_PATH_OVERRIDE
set -uo pipefail

MIN_MEM_GB=24  # 12분 녹음 실측 피크 footprint 17.3GiB(캐시 상한 2GB) + OS 여유 6GiB 를 RAM 등급으로 올림
MIN_MLX_AUDIO=0.6.2
HF_HUB="${ASR_HF_HUB:-$HOME/.cache/huggingface/hub}"
VENV_DIR="${ASR_VENV:-$HOME/.local/share/asr/venv}"
[ -n "${ASR_PATH_OVERRIDE:-}" ] && PATH="$ASR_PATH_OVERRIDE"

os="${ASR_UNAME_S:-$(uname -s)}"
arm64="${ASR_ARM64:-$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)}"
mem_bytes="${ASR_MEM_BYTES:-$(sysctl -n hw.memsize 2>/dev/null || echo 0)}"
mem_gb=$((mem_bytes / 1024 / 1024 / 1024))
disk_free_gb=$(/bin/df -k "$HOME" 2>/dev/null | awk 'NR==2 {print int($4 / 1024 / 1024)}')

arch=x86_64
[ "$arm64" = 1 ] && arch=arm64

echo "OS=$os"
echo "ARCH=$arch"
echo "MEM_GB=$mem_gb"
echo "DISK_FREE_GB=${disk_free_gb:-0}"

reason=""
if [ "$os" != Darwin ]; then
  reason="OS 가 $os 입니다. macOS 전용입니다."
elif [ "$arm64" != 1 ]; then
  reason="Apple Silicon(M1 이상)이 아닙니다."
elif [ "$mem_gb" -lt "$MIN_MEM_GB" ]; then
  reason="메모리 ${mem_gb}GB — 10분 조각 처리에 최소 ${MIN_MEM_GB}GB가 필요합니다."
fi
if [ -n "$reason" ]; then
  echo "UNSUPPORTED_REASON=$reason"
  echo "STATUS=unsupported"
  exit 20
fi

missing=0

# 도구
if command -v brew >/dev/null 2>&1; then echo "BREW=present"; else echo "BREW=absent"; fi
if command -v ffmpeg >/dev/null 2>&1; then echo "FFMPEG=present"; else echo "FFMPEG=absent"; missing=1; fi

python_path=""
for cand in python3.12 python3.13 python3; do
  p="$(command -v "$cand" 2>/dev/null)" || continue
  ver="$("$p" --version 2>&1 | sed -n 's/^Python \([0-9]*\)\.\([0-9]*\).*/\1 \2/p')"
  read -r major minor <<<"$ver"
  if [ -n "${major:-}" ] && { [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "${minor:-0}" -ge 12 ]; }; }; then
    python_path="$p"
    break
  fi
done
if [ -n "$python_path" ]; then echo "PYTHON=$python_path"; else echo "PYTHON=absent"; missing=1; fi

# 설치
# mlx-audio: import 가 돼야 하고 버전이 MIN_MLX_AUDIO 이상이어야 한다.
# import 실패면 absent, 버전이 낮으면 그 버전을 내고 둘 다 missing 이다.
version_ge() { # version_ge <버전> <최소 버전> — 앞 세 자리를 숫자로 비교
  local a b c x y z
  IFS=. read -r a b c <<<"$1"
  IFS=. read -r x y z <<<"$2"
  a=${a%%[^0-9]*}; b=${b%%[^0-9]*}; c=${c%%[^0-9]*}
  [ "${a:-0}" -ne "$x" ] && { [ "${a:-0}" -gt "$x" ]; return; }
  [ "${b:-0}" -ne "$y" ] && { [ "${b:-0}" -gt "$y" ]; return; }
  [ "${c:-0}" -ge "$z" ]
}
mlx_audio=absent
if [ -x "$VENV_DIR/bin/python" ]; then
  echo "VENV=present"
  v="$("$VENV_DIR/bin/python" -c "import mlx_audio, importlib.metadata as m; print(m.version('mlx-audio'))" 2>/dev/null | tail -n1)" \
    && [ -n "$v" ] && mlx_audio="$v"
else
  echo "VENV=absent"
  missing=1
fi
echo "MLX_AUDIO=$mlx_audio"
{ [ "$mlx_audio" = absent ] || ! version_ge "$mlx_audio" "$MIN_MLX_AUDIO"; } && missing=1

# 모델: 필수 파일이 심볼릭 링크까지 실제 파일로 풀리고, blobs 에 *.incomplete 가 없어야 present
model_present() { # model_present <org--name> <config.json 필요 여부 0/1> <필수 파일 glob>
  local dir="$HF_HUB/models--$1" need_config="$2" required="$3" snap f ok
  ls "$dir"/blobs/*.incomplete >/dev/null 2>&1 && return 1
  for snap in "$dir"/snapshots/*/; do
    [ -d "$snap" ] || continue
    [ "$need_config" = 1 ] && [ ! -e "${snap}config.json" ] && continue
    ok=0
    for f in "$snap"$required; do
      [ -e "$f" ] && ok=1 || { ok=0; break; }
    done
    [ "$ok" -eq 1 ] && return 0
  done
  return 1
}

report_model() { # report_model <KEY> <org--name> <need_config> <glob>
  if model_present "$2" "$3" "$4"; then echo "$1=present"; else echo "$1=missing"; missing=1; fi
}
report_model MODEL_VIBEVOICE mlx-community--VibeVoice-ASR-8bit 1 '*.safetensors'
report_model MODEL_VAD mlx-community--silero-vad 0 model.safetensors
report_model MODEL_ECAPA aufklarer--SpeechBrain-ECAPA-VoxCeleb-20M-MLX 0 model.safetensors

if [ "$missing" -eq 0 ]; then
  echo "STATUS=ready"
  exit 0
fi
echo "STATUS=missing"
exit 10
