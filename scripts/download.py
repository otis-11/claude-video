#!/usr/bin/env python3
"""Download a video via yt-dlp, or resolve a local file path.

Also fetches subtitles (manual first, then auto-generated) in VTT format so
transcribe.py can parse them without needing Whisper.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}

# Browsers yt-dlp can read cookies from via --cookies-from-browser.
SUPPORTED_BROWSERS = {
    "brave", "chrome", "chromium", "edge", "firefox", "opera", "safari",
    "vivaldi", "whale",
}


def _config_value(name: str) -> str | None:
    """Read a setting from the environment, then ~/.config/watch/.env, then ./.env.

    Mirrors whisper.load_api_key's resolution order so cookie config lives in the
    same place as the API keys.
    """
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()

    for path in (Path.home() / ".config" / "watch" / ".env", Path.cwd() / ".env"):
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, raw = line.partition("=")
                if key.strip() != name:
                    continue
                raw = raw.strip()
                if len(raw) >= 2 and raw[0] in ('"', "'") and raw[-1] == raw[0]:
                    raw = raw[1:-1]
                return raw or None
        except OSError:
            continue
    return None


def resolve_cookie_args(
    cookies_from_browser: str | None = None,
    cookies_file: str | None = None,
) -> list[str]:
    """Build yt-dlp cookie flags for login-walled sites (X, Facebook, Instagram).

    Resolution order (first hit wins):
      1. --cookies-from-browser passed explicitly
      2. --cookies file passed explicitly
      3. WATCH_COOKIES_FROM_BROWSER in env / .env  (set once, applies always)
      4. WATCH_COOKIES_FILE in env / .env
    Returns [] when nothing is configured (current public-only behavior).

    Explicit flags take precedence over env/.env *as a group*: env is consulted
    only when neither flag is given, so a one-off flag can't be shadowed by a
    persistent .env setting. A bad value from an explicit flag is fatal (the
    user asked for it this run); a bad value from env only warns and falls
    through to public-only mode, so a stale .env can't brick every download.
    """
    browser = cookies_from_browser
    cfile = cookies_file
    from_env = False
    if not browser and not cfile:
        browser = _config_value("WATCH_COOKIES_FROM_BROWSER")
        cfile = _config_value("WATCH_COOKIES_FILE")
        from_env = True

    if browser:
        spec = browser.strip()
        base = spec.split(":", 1)[0].split("+", 1)[0].strip().lower()
        if base not in SUPPORTED_BROWSERS:
            supported = ", ".join(sorted(SUPPORTED_BROWSERS))
            if from_env:
                print(
                    f"[watch] warning: WATCH_COOKIES_FROM_BROWSER={spec!r} is not a "
                    f"supported browser ({supported}); ignoring and downloading "
                    "public-only. Fix it in ~/.config/watch/.env.",
                    file=sys.stderr,
                )
                return []
            raise SystemExit(f"Unknown browser for cookies: {spec!r}. Supported: {supported}")
        return ["--cookies-from-browser", spec]

    if cfile:
        p = Path(cfile).expanduser()
        if not p.exists():
            if from_env:
                print(
                    f"[watch] warning: WATCH_COOKIES_FILE points at a missing file "
                    f"({p}); ignoring and downloading public-only. Fix it in "
                    "~/.config/watch/.env.",
                    file=sys.stderr,
                )
                return []
            raise SystemExit(f"Cookies file not found: {p}")
        return ["--cookies", str(p)]

    return []


def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        print(
            f"[watch] warning: {p.suffix} is not a known video extension, proceeding anyway",
            file=sys.stderr,
        )
    return {
        "video_path": str(p),
        "subtitle_path": None,
        "info": {"title": p.name, "url": str(p)},
        "downloaded": False,
    }


def _pick_subtitle(out_dir: Path) -> Path | None:
    candidates = sorted(out_dir.glob("video*.vtt"))
    if not candidates:
        return None
    preferred = [c for c in candidates if ".en" in c.name]
    return preferred[0] if preferred else candidates[0]


def _pick_video(out_dir: Path) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm", ".mov"):
        for candidate in out_dir.glob(f"video*{ext}"):
            return candidate
    for candidate in out_dir.glob("video.*"):
        if candidate.suffix.lower() in VIDEO_EXTS:
            return candidate
    return None


def download_url(
    url: str,
    out_dir: Path,
    cookies_from_browser: str | None = None,
    cookies_file: str | None = None,
) -> dict:
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed. Install with: brew install yt-dlp")

    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "video.%(ext)s")

    cookie_args = resolve_cookie_args(cookies_from_browser, cookies_file)
    if cookie_args:
        how = cookie_args[1]
        print(f"[watch] using cookies ({cookie_args[0].lstrip('-')}: {how})", file=sys.stderr)

    cmd = [
        "yt-dlp",
        "-N", "8",
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en,en-US,en-GB,en-orig",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        *cookie_args,
        "-o", output_template,
        "--",
        url,
    ]

    # yt-dlp may exit non-zero if a subtitle variant fails (e.g. 429) even when
    # the video itself downloaded fine. Treat "video file present" as success.
    result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    video = _pick_video(out_dir)
    if video is None:
        raise SystemExit(
            f"yt-dlp did not produce a video file in {out_dir} (exit {result.returncode})"
        )

    subtitle = _pick_subtitle(out_dir)
    info_path = out_dir / "video.info.json"
    info: dict = {}
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            info = {
                "title": raw.get("title"),
                "uploader": raw.get("uploader") or raw.get("channel"),
                "duration": raw.get("duration"),
                "url": raw.get("webpage_url") or url,
            }
        except Exception as exc:
            print(f"[watch] info.json parse failed: {exc}", file=sys.stderr)
            info = {"url": url}

    return {
        "video_path": str(video),
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": info or {"url": url},
        "downloaded": True,
    }


def download(
    source: str,
    out_dir: Path,
    cookies_from_browser: str | None = None,
    cookies_file: str | None = None,
) -> dict:
    if is_url(source):
        return download_url(source, out_dir, cookies_from_browser, cookies_file)
    return resolve_local(source)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: download.py <url-or-path> <out-dir>", file=sys.stderr)
        raise SystemExit(2)
    result = download(sys.argv[1], Path(sys.argv[2]))
    print(json.dumps(result, indent=2))
