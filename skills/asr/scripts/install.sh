#!/usr/bin/env bash
# 빠진 항목만 순서대로 설치한다. 사용법: install.sh [--dry-run]
# 종료 코드: 0 성공 / 1 설치 후에도 ready 아님·단계 실패 / 2 Homebrew 없음 / 3 디스크 부족 / 20 미지원 환경
# 덮어쓰기: ASR_VENV, ASR_HF_HUB (check_env.sh 와 같은 변수)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ASR_VENV:-$HOME/.local/share/asr/venv}"
[ -n "${ASR_HF_HUB:-}" ] && export HF_HUB_CACHE="$ASR_HF_HUB"
MLX_AUDIO_PIN=0.6.5
REPO_VIBEVOICE=mlx-community/VibeVoice-ASR-8bit
REPO_VAD=mlx-community/silero-vad
REPO_ECAPA=aufklarer/SpeechBrain-ECAPA-VoxCeleb-20M-MLX
MARGIN_GB=2

dry_run=0
case "${1:-}" in
  "") ;;
  --dry-run) dry_run=1 ;;
  *) echo "사용법: install.sh [--dry-run]" >&2; exit 64 ;;
esac

env_out="$("$HERE/check_env.sh")"
get() { printf '%s\n' "$env_out" | sed -n "s/^$1=//p" | head -n1; }

if [ "$(get STATUS)" = unsupported ]; then
  echo "설치할 수 없는 환경입니다: $(get UNSUPPORTED_REASON)" >&2
  exit 20
fi

# 할 일: "이름|설명|예상GB" 형식. 예상GB 가 -이면 합산하지 않는다.
todo=()
need_brew=0
[ "$(get FFMPEG)" = absent ] && { todo+=("ffmpeg|brew install ffmpeg|-"); need_brew=1; }
[ "$(get PYTHON)" = absent ] && { todo+=("python|brew install python@3.12|-"); need_brew=1; }
[ "$(get VENV)" = absent ] && todo+=("venv|가상환경 만들기 ($VENV_DIR)|0.5")
[ "$(get MLX_AUDIO)" != "$MLX_AUDIO_PIN" ] && todo+=("mlx|pip install mlx-audio==$MLX_AUDIO_PIN|-")
[ "$(get MODEL_VIBEVOICE)" = missing ] && todo+=("vibevoice|모델 다운로드 $REPO_VIBEVOICE|9.5")
[ "$(get MODEL_VAD)" = missing ] && todo+=("vad|모델 다운로드 $REPO_VAD|0.002")
[ "$(get MODEL_ECAPA)" = missing ] && todo+=("ecapa|모델 다운로드 $REPO_ECAPA (model.safetensors)|0.042")

if [ "$need_brew" = 1 ] && [ "$(get BREW)" = absent ]; then
  echo "Homebrew가 없어 자동 설치할 수 없습니다. https://brew.sh 에서 설치한 뒤 다시 실행해 주세요."
  exit 2
fi

total=${#todo[@]}
if [ "$total" -eq 0 ]; then
  echo "설치할 항목이 없습니다."
  exit 0
fi

# mlx 패키지 용량은 venv 0.5GB 에 포함해 센다.
est_gb="$(printf '%s\n' "${todo[@]}" | awk -F'|' '$3 != "-" {s += $3} END {printf "%.2f", s}')"
disk_free="$(get DISK_FREE_GB)"
disk_ok="$(awk -v f="${disk_free:-0}" -v e="$est_gb" -v m="$MARGIN_GB" 'BEGIN {print (f >= e + m) ? 1 : 0}')"

if [ "$dry_run" = 1 ]; then
  echo "할 일 ${total}개:"
  n=0
  for item in "${todo[@]}"; do
    n=$((n + 1))
    IFS='|' read -r _ desc gb <<<"$item"
    if [ "$gb" = "-" ]; then echo "  $n. $desc"; else echo "  $n. $desc (약 ${gb}GB)"; fi
  done
  echo "예상 용량 합계: 약 ${est_gb}GB (brew 패키지 제외)"
  echo "디스크 여유: ${disk_free:-0}GB (필요: 예상 용량 + ${MARGIN_GB}GB)"
  [ "$disk_ok" = 1 ] || { echo "디스크 여유가 부족합니다." >&2; exit 3; }
  exit 0
fi

if [ "$disk_ok" != 1 ]; then
  echo "디스크 여유가 부족합니다. 여유 ${disk_free:-0}GB, 필요 약 ${est_gb}GB + ${MARGIN_GB}GB. 설치하지 않습니다." >&2
  exit 3
fi

fail() { echo "실패: $1" >&2; exit 1; }

hf_download() { # hf_download <repo> [파일]
  local hf="$VENV_DIR/bin/hf"
  if [ -x "$hf" ]; then "$hf" download "$@"
  else "$VENV_DIR/bin/huggingface-cli" download "$@"; fi
}

n=0
for item in "${todo[@]}"; do
  n=$((n + 1))
  IFS='|' read -r name desc _ <<<"$item"
  echo "STEP $n/$total $desc"
  case "$name" in
    ffmpeg) brew install ffmpeg || fail "ffmpeg 설치" ;;
    python) brew install python@3.12 || fail "python@3.12 설치" ;;
    venv)
      py="$(command -v python3.12 || true)"
      [ -n "$py" ] || py="$(brew --prefix python@3.12 2>/dev/null)/bin/python3.12"
      [ -x "$py" ] || py="$(get PYTHON)"
      [ -x "$py" ] || fail "Python 3.12 이상을 찾지 못했습니다"
      mkdir -p "$(dirname "$VENV_DIR")" && "$py" -m venv "$VENV_DIR" || fail "venv 생성" ;;
    mlx)
      [ -x "$VENV_DIR/bin/python" ] || fail "venv 가 없습니다"
      "$VENV_DIR/bin/python" -m pip install "mlx-audio==$MLX_AUDIO_PIN" || fail "mlx-audio 설치"
      if ! "$VENV_DIR/bin/hf" --version >/dev/null 2>&1; then
        "$VENV_DIR/bin/python" -m pip install --upgrade huggingface_hub || fail "huggingface_hub 설치"
      fi ;;
    vibevoice) hf_download "$REPO_VIBEVOICE" || fail "VibeVoice 다운로드" ;;
    vad) hf_download "$REPO_VAD" || fail "VAD 다운로드" ;;
    ecapa) hf_download "$REPO_ECAPA" model.safetensors || fail "ECAPA 다운로드" ;;
  esac
done

echo "설치 후 환경을 다시 점검합니다."
"$HERE/check_env.sh"
[ $? -eq 0 ] || exit 1
