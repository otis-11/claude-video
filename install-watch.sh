#!/usr/bin/env bash
# One-shot installer for the /watch Claude Code skill.
# Installs the skill into ~/.claude/skills/watch AND its runtime binaries
# (ffmpeg, ffprobe, yt-dlp) into ~/.local/bin. No Homebrew required.
# macOS only (Apple Silicon or Intel). Run:  bash install-watch.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
SKILL_DIR="$HOME/.claude/skills/watch"

echo "[install] source:      $SRC"
echo "[install] skill  ->    $SKILL_DIR"
echo "[install] binaries ->  $BIN"
mkdir -p "$BIN" "$HOME/.claude/skills"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "[install] This installer targets macOS. On Linux/Windows, install ffmpeg + yt-dlp"
  echo "          via your package manager and copy this folder to ~/.claude/skills/watch."
  exit 1
fi

case "$(uname -m)" in
  arm64)  FF_ARCH="arm64" ;;
  x86_64) FF_ARCH="amd64" ;;
  *) echo "[install] unsupported arch: $(uname -m)"; exit 1 ;;
esac

have() { command -v "$1" >/dev/null 2>&1; }

# --- ffmpeg / ffprobe (static builds) ---
install_ff() {
  local name="$1"
  if have "$name"; then echo "[install] $name already present: $(command -v "$name")"; return; fi
  echo "[install] downloading $name ($FF_ARCH)…"
  curl -fL --retry 3 -o "$BIN/$name.zip" \
    "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$FF_ARCH/release/$name.zip"
  ( cd "$BIN" && unzip -o -q "$name.zip" && rm -f "$name.zip" && chmod +x "$name" )
}
install_ff ffmpeg
install_ff ffprobe

# --- yt-dlp (universal macOS binary) ---
if have yt-dlp; then
  echo "[install] yt-dlp already present: $(command -v yt-dlp)"
else
  echo "[install] downloading yt-dlp…"
  curl -fL --retry 3 -o "$BIN/yt-dlp" \
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
  chmod +x "$BIN/yt-dlp"
fi
xattr -c "$BIN/ffmpeg" "$BIN/ffprobe" "$BIN/yt-dlp" 2>/dev/null || true

# --- PATH: ensure ~/.local/bin is on it ---
if ! printf ':%s:' "$PATH" | grep -q ":$BIN:"; then
  RC="$HOME/.zshrc"; [ -n "${ZSH_VERSION:-}" ] || RC="$HOME/.zshrc"
  grep -q '.local/bin' "$RC" 2>/dev/null || \
    printf '\n# Added by /watch installer\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC"
  echo "[install] added ~/.local/bin to PATH in $RC"
fi
export PATH="$BIN:$PATH"

# --- skill files ---
echo "[install] installing skill files…"
if have rsync; then
  rsync -a --delete --exclude='.git' --exclude='dist' --exclude='install-watch.sh' "$SRC/" "$SKILL_DIR/"
else
  mkdir -p "$SKILL_DIR"
  ( cd "$SRC" && tar --exclude='.git' --exclude='dist' -cf - . ) | ( cd "$SKILL_DIR" && tar -xf - )
fi

# --- config (frames-only by default; add a key later for transcripts) ---
python3 "$SKILL_DIR/scripts/setup.py" >/dev/null 2>&1 || true
ENV="$HOME/.config/watch/.env"
if [ -f "$ENV" ]; then
  grep -q '^SETUP_COMPLETE=' "$ENV" || printf 'SETUP_COMPLETE=true\n' >> "$ENV"
  chmod 600 "$ENV"
fi

# --- verify ---
echo "[install] verifying…"
ffmpeg -version | head -1
ffprobe -version | head -1
printf 'yt-dlp %s\n' "$(yt-dlp --version)"
set +e; python3 "$SKILL_DIR/scripts/setup.py" --check >/dev/null 2>&1; rc=$?; set -e
echo "[install] preflight exit $rc (0 = fully ready; 3 = deps OK, frames-only / no Whisper key — that's fine)"

cat <<'DONE'

[install] DONE.
  - Restart Claude Code (skills load at startup), then run:  /watch <video-url-or-path>
  - Frames-only by default. For spoken-word transcripts, add GROQ_API_KEY to ~/.config/watch/.env
  - For login-walled X / Facebook / Instagram videos, set in ~/.config/watch/.env:
        WATCH_COOKIES_FROM_BROWSER=chrome    # or safari, firefox, edge, brave
DONE
