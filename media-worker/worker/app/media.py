"""Media processors: TTS (Kokoro, Piper fallback), transcription (faster-whisper), Reel rendering (FFmpeg).

Each processor receives (job_dir, params, job_id) and returns a JSON-serialisable result.
Inputs are only fetched from plain http(s) URLs: the worker never scrapes platforms.
Whether a source may be used is decided upstream (rights_status in n8n), not here.
"""

import json
import math
import os
import subprocess
import textwrap
import threading
from pathlib import Path
from urllib.parse import urlparse

import requests

W, H, FPS = 1080, 1920, 30
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
MAX_DOWNLOAD_BYTES = int(float(os.environ.get("MAX_DOWNLOAD_MB", "500")) * 1024 * 1024)
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small.en")
PIPER_VOICE = os.environ.get("PIPER_VOICE", "/opt/piper/en_US-lessac-medium.onnx")
TTS_ENGINE = os.environ.get("TTS_ENGINE", "kokoro")
KOKORO_MODEL = os.environ.get("KOKORO_MODEL", "/opt/kokoro/kokoro-v1.0.int8.onnx")
KOKORO_VOICES = os.environ.get("KOKORO_VOICES", "/opt/kokoro/voices-v1.0.bin")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "am_michael")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/srv/media"))
# Hosts whose videos are fetched with yt-dlp (only the requested section is downloaded)
SOURCE_HOSTS = [h.strip() for h in os.environ.get("SOURCE_HOSTS", "youtube.com,youtu.be,twitch.tv,kick.com").split(",") if h.strip()]
MAX_CLIP_SECONDS = float(os.environ.get("MAX_CLIP_SECONDS", "75"))

_whisper = None
_whisper_lock = threading.Lock()
_kokoro = None
_kokoro_lock = threading.Lock()


# ---------------------------------------------------------------- helpers
def run(cmd: list[str], cwd: Path | None = None, stdin: str | None = None) -> str:
    p = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr[-1200:]}")
    return p.stdout


def probe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(out)["format"]["duration"])


def public_url(job_id: str, name: str) -> str:
    return f"{PUBLIC_BASE_URL}/files/{job_id}/{name}"


def download(url: str, dest: Path) -> Path:
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"only http(s) URLs are allowed: {url}")
    size = 0
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise ValueError(f"download larger than {MAX_DOWNLOAD_BYTES // 1048576} MB: {url}")
                f.write(chunk)
    return dest


def is_platform_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SOURCE_HOSTS)


def fetch_audio(url: str, job_dir: Path) -> Path:
    """Audio track of a platform video (yt-dlp) or of a plain media URL, as 16 kHz mono wav."""
    wav = job_dir / "audio.wav"
    if is_platform_url(url):
        run(["yt-dlp", "--no-playlist", "--no-progress", "-f", "ba[ext=m4a]/ba/b",
             "--max-filesize", f"{MAX_DOWNLOAD_BYTES // 1048576}M", "-o", str(job_dir / "source.%(ext)s"), url])
        src = next(job_dir.glob("source.*"))
    else:
        src = download(url, job_dir / "source")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    src.unlink(missing_ok=True)
    return wav


def is_short_clip_url(url: str) -> bool:
    """Twitch / Kick clips: short files, downloaded whole and cut locally."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    return host == "clips.twitch.tv" or ("twitch.tv" in host and "/clip/" in u.path) or ("kick.com" in host and "clip" in u.path)


def cut(src: Path, start: float, end: float, out: Path) -> Path:
    """Frame-accurate cut with re-encode (no glitches from cutting between keyframes)."""
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "192k", str(out)])
    src.unlink(missing_ok=True)
    return out


def fetch_section(url: str, start: float, end: float, out: Path) -> Path:
    """Only the [start, end] section of a video, with audio (mp4)."""
    if end <= start:
        raise ValueError("clip end must be greater than start")
    if is_platform_url(url):
        fmt = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b"
        tmp = out.with_name("section_src")
        if is_short_clip_url(url):
            # whole clip, then our own accurate cut: section downloads of clip files can glitch mid-video
            run(["yt-dlp", "--no-playlist", "--no-progress", "-f", fmt, "--merge-output-format", "mp4",
                 "--max-filesize", f"{MAX_DOWNLOAD_BYTES // 1048576}M", "-o", str(tmp) + ".%(ext)s", url])
            return cut(next(out.parent.glob("section_src.*")), start, end, out)
        run(["yt-dlp", "--no-playlist", "--no-progress", "-f", fmt,
             "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
             "--merge-output-format", "mp4", "-o", str(tmp) + ".%(ext)s", url])
        src = next(out.parent.glob("section_src.*"))
        src.rename(out)
        return out
    return cut(download(url, out.with_name("section_full")), start, end, out)


def get_whisper():
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel

            _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                                    download_root=str(DATA_DIR / "models"))
        return _whisper


def whisper_words(audio: Path, language: str | None = "en") -> tuple[list[dict], list[dict], float]:
    segments, info = get_whisper().transcribe(str(audio), language=language, word_timestamps=True, vad_filter=True)
    segs, words = [], []
    for s in segments:
        segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        for w in s.words or []:
            words.append({"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()})
    return segs, words, round(info.duration, 2)


# ---------------------------------------------------------------- TTS
def get_kokoro():
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            from kokoro_onnx import Kokoro

            _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
        return _kokoro


def synth(text: str, out: Path, speed: float = 1.0, voice: str | None = None, engine: str | None = None) -> Path:
    text = " ".join(text.split())
    if not text:
        raise ValueError("voiceover text is empty")
    if len(text) > 3000:
        raise ValueError("voiceover text longer than 3000 characters")
    engine = engine or TTS_ENGINE
    if engine == "kokoro":
        import soundfile as sf

        samples, rate = get_kokoro().create(text, voice=voice or KOKORO_VOICE, speed=min(max(speed, 0.5), 2.0),
                                            lang="en-us")
        sf.write(str(out), samples, rate)
    elif engine == "piper":
        # Piper: length_scale < 1 = faster speech
        run(["piper", "--model", PIPER_VOICE, "--output_file", str(out),
             "--length_scale", f"{1 / max(speed, 0.5):.3f}"], stdin=text)
    else:
        raise ValueError(f"unknown tts engine: {engine}")
    return out


def process_tts(job_dir: Path, params: dict, job_id: str) -> dict:
    out = synth(params.get("text", ""), job_dir / "voice.wav", float(params.get("speed", 1.0)),
                params.get("voice"), params.get("engine"))
    return {"audio_url": public_url(job_id, out.name), "duration": round(probe_duration(out), 2)}


# ---------------------------------------------------------------- transcription
def process_transcribe(job_dir: Path, params: dict, job_id: str) -> dict:
    # source_url: platform page (yt-dlp, audio only) | media_url: plain file URL
    url = params.get("source_url") or params.get("media_url")
    if not url:
        raise ValueError("source_url or media_url is required")
    audio = fetch_audio(url, job_dir)
    segs, words, duration = whisper_words(audio, params.get("language", "en"))
    payload = {"duration": duration, "segments": segs, "words": words}
    (job_dir / "transcript.json").write_text(json.dumps(payload, ensure_ascii=False))
    audio.unlink(missing_ok=True)
    return {"transcript_url": public_url(job_id, "transcript.json"), "duration": duration,
            "segments": segs, "text": " ".join(s["text"] for s in segs)}


# ---------------------------------------------------------------- subtitles (ASS)
def ass_time(t: float) -> str:
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[dict], hook: str, hook_seconds: float, watermark: str, duration: float,
              words_per_caption: int = 3, credit: str = "") -> str:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,82,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,2,2,80,80,560,1
Style: Hook,DejaVu Sans,70,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0000000,-1,0,0,0,100,100,0,0,3,18,0,8,70,70,240,1
Style: Watermark,DejaVu Sans,40,&H60FFFFFF,&H60FFFFFF,&H80000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,40,40,140,1
Style: Credit,DejaVu Sans,36,&H30FFFFFF,&H30FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,40,40,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook:
        wrapped = "\\N".join(textwrap.wrap(ass_escape(hook), width=24))
        lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(min(hook_seconds, duration))},Hook,,0,0,0,,{wrapped}")
    for i in range(0, len(words), words_per_caption):
        chunk = words[i:i + words_per_caption]
        text = ass_escape(" ".join(w["word"] for w in chunk)).upper()
        start = chunk[0]["start"]
        end = words[i + words_per_caption]["start"] if i + words_per_caption < len(words) else chunk[-1]["end"]
        if end > start:
            lines.append(f"Dialogue: 1,{ass_time(start)},{ass_time(min(end, duration))},Caption,,0,0,0,,{text}")
    if watermark:
        lines.append(f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Watermark,,0,0,0,,{ass_escape(watermark)}")
    if credit:
        lines.append(f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Credit,,0,0,0,,{ass_escape(credit)}")
    return header + "\n".join(lines) + "\n"


# ---------------------------------------------------------------- render
COVER = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},format=yuv420p"


def make_segment(job_dir: Path, i: int, visual: dict, seconds: float) -> Path:
    out = job_dir / f"seg_{i:02d}.mp4"
    kind = visual.get("type", "color")
    enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS), "-t", f"{seconds:.3f}"]
    if kind == "color":
        color = visual.get("color", "#111111")
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:r={FPS}",
               "-vf", "format=yuv420p", *enc, str(out)]
    elif kind == "image":
        src = download(visual["url"], job_dir / f"src_{i:02d}")
        # slow zoom-in keeps still images alive
        frames = max(int(seconds * FPS), 1)
        zoom = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                f"zoompan=z='min(zoom+0.0006,1.12)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
                f"setsar=1,format=yuv420p")
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src), "-vf", zoom, *enc, str(out)]
    elif kind == "video":
        src = download(visual["url"], job_dir / f"src_{i:02d}")
        start = float(visual.get("start", 0))
        cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-ss", f"{start:.3f}", "-i", str(src),
               "-vf", COVER, *enc, str(out)]
    else:
        raise ValueError(f"unknown visual type: {kind}")
    run(cmd)
    return out


def plan_durations(visuals: list[dict], total: float) -> list[float]:
    fixed = [float(v["duration"]) if v.get("duration") else None for v in visuals]
    used = sum(d for d in fixed if d)
    free = [i for i, d in enumerate(fixed) if d is None]
    rest = max(total - used, 0)
    for i in free:
        fixed[i] = rest / len(free) if rest > 0 else 1.0
    # make sure the visuals cover the whole audio: stretch the last one
    covered = sum(fixed)
    if covered < total:
        fixed[-1] += total - covered
    return [max(d, 0.5) for d in fixed]


def process_render(job_dir: Path, params: dict, job_id: str) -> dict:
    if params.get("clip"):
        return render_clip(job_dir, params, job_id)
    max_duration = float(params.get("max_duration", 90))
    visuals = params.get("visuals") or [{"type": "color", "color": "#111111"}]

    # 1. audio: TTS voiceover or provided audio file
    vo = params.get("voiceover") or {}
    voice = job_dir / "voice.wav"
    if vo.get("text"):
        synth(vo["text"], voice, float(vo.get("speed", 1.0)), vo.get("voice"), vo.get("engine"))
    elif params.get("audio_url"):
        raw = download(params["audio_url"], job_dir / "audio_src")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-ac", "1", "-ar", "48000", str(voice)])
    else:
        raise ValueError("voiceover.text or audio_url is required")
    duration = min(probe_duration(voice), max_duration)

    # 2. captions from the voiceover itself (word timings), so they always match the audio
    words = []
    if params.get("captions", True):
        _, words, _ = whisper_words(voice, "en")
    (job_dir / "subs.ass").write_text(build_ass(
        words, params.get("hook", ""), float(params.get("hook_seconds", 3)), params.get("watermark", ""), duration,
        int(params.get("words_per_caption", 3))))

    # 3. visual track
    segs = [make_segment(job_dir, i, v, d) for i, (v, d) in enumerate(zip(visuals, plan_durations(visuals, duration)))]
    (job_dir / "concat.txt").write_text("".join(f"file '{s.name}'\n" for s in segs))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "base.mp4"],
        cwd=job_dir)

    # 4. final mux: subtitles burned in, AAC 48 kHz, faststart (Instagram Reels spec)
    run(["ffmpeg", "-y", "-v", "error", "-i", "base.mp4", "-i", "voice.wav", "-vf", "ass=subs.ass",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-t", f"{duration:.3f}",
         "-movflags", "+faststart", "reel.mp4"], cwd=job_dir)
    return finish(job_dir, job_id, duration, len(words), keep=("voice.wav",))


def finish(job_dir: Path, job_id: str, duration: float, captions: int, keep: tuple = (), extra: dict | None = None) -> dict:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, duration / 2):.2f}", "-i", "reel.mp4", "-frames:v", "1",
         "-q:v", "3", "cover.jpg"], cwd=job_dir)
    for p in job_dir.iterdir():  # keep only deliverables
        if p.name not in ("reel.mp4", "cover.jpg", "subs.ass", *keep):
            p.unlink(missing_ok=True)
    reel = job_dir / "reel.mp4"
    return {
        "video_url": public_url(job_id, "reel.mp4"),
        "cover_url": public_url(job_id, "cover.jpg"),
        "duration": round(probe_duration(reel), 2),
        "width": W,
        "height": H,
        "size_bytes": reel.stat().st_size,
        "captions": captions,
        **(extra or {}),
    }


# ---------------------------------------------------------------- render: clip mode
# Reel layout (1080x1920):
#   y ~150-330  hook for the first seconds, or a short meme-style line (top_text) for the whole Reel
#   y ~335      page watermark (after the hook, or from the start with top_text)
#   y ~395      small source credit
#   centre      the clip, wider than the frame (sides cropped) over a blurred copy of itself,
#               slow zoom-in + an optional punch zoom on the key moment
#   y ~1500     big captions, current word highlighted
# Our voice-over plays over the first seconds of the moving clip (original audio ducked under it):
# no frozen intro frame, the clip is in motion from frame one.
AUDIO_OUT = ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
HIGHLIGHT = "&H0000E6FF&"  # ASS colours are BGR: yellow #FFE600
ACCENT = "&H00FF5CB4&"  # brand purple #B45CFF, for the key word of the meme line (top_accent, "clean" style)
POP_YELLOW = "&H0021D2FF&"  # #FFD221: key word, underline and sparks of the "pop" meme line

# Style line template: margins come from the layout (where the clip sits in the frame)
CLIP_ASS_STYLES = """Style: Caption,DejaVu Sans,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,8,3,2,70,70,{caption_v},1
Style: Hook,DejaVu Sans,92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,22,0,8,60,60,{hook_v},1
Style: Top,Montserrat ExtraBold,92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,5,3,8,60,60,{top_v},1
Style: Watermark,DejaVu Sans,46,&H50FFFFFF,&H50FFFFFF,&H90000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,8,40,40,{watermark_v},1
Style: Credit,DejaVu Sans,34,&H40FFFFFF,&H40FFFFFF,&H90000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,40,40,{credit_v},1
Style: Pop,Montserrat Black,88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,0,0,0,0,100,100,0,0,1,4,5,8,40,40,0,1
Style: PopHandle,Montserrat ExtraBold,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,0,0,0,0,100,100,0,0,1,3,2,8,0,0,0,1
Style: PopCredit,DejaVu Sans,30,&H40FFFFFF,&H40FFFFFF,&H90000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,0,0,0,1
Style: Draw,DejaVu Sans,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"""


def clip_layout(fg_h: int) -> dict:
    """ASS vertical margins around the clip: texts sit above it, captions straddle its bottom edge
    (kept above Instagram's caption/buttons area, the bottom ~20% of the screen)."""
    top = (H - fg_h) // 2
    bottom = top + fg_h
    return {
        "hook_v": max(top - 340, 100),
        "top_v": max(top - 345, 90),
        "watermark_v": max(top - 110, 230),
        "credit_v": max(top - 48, 290),
        "caption_v": max(H - bottom - 60, 390),
    }


def has_audio(path: Path) -> bool:
    out = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
               str(path)])
    return bool(out.strip())


def video_size(path: Path) -> tuple[int, int]:
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
               "-of", "json", str(path)])
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def balanced_wrap(text: str, width: int) -> list[str]:
    """Same number of lines as textwrap at `width`, but with lines of similar length (no lone last word)."""
    lines = textwrap.wrap(text, width=width, break_long_words=False)
    for w in range(max(len(text) // max(len(lines), 1), 1), width):
        candidate = textwrap.wrap(text, width=w, break_long_words=False)
        if len(candidate) == len(lines):
            return candidate
    return lines


def accent_words(line: str, accent: str, colour: str = ACCENT) -> str:
    """Colours the words of `accent` (e.g. "never") inside one line of the meme text, ignoring case and punctuation."""
    keys = {w.strip(".,!?'\"").lower() for w in accent.split()} - {""}
    if not keys:
        return line
    return " ".join(f"{{\\c{colour}}}{w}{{\\c&H00FFFFFF&}}" if w.strip(".,!?'\"").lower() in keys else w
                    for w in line.split(" "))


def pop_top_events(text: str, accent: str, watermark: str, credit: str, duration: float) -> list[str]:
    """"Pop" meme line: tilted bold text with the key word in yellow, a yellow swoosh under it, sparks on both
    sides, then the page handle (small yellow underline) and the source credit. Sits above the clip."""
    lines = balanced_wrap(ass_escape(text), 18)
    n = len(lines)
    fs = 88 if n <= 2 else 74
    lh, y0, ang = fs * 1.05, 92, 5
    cy = y0 + lh * n / 2
    t0, t1 = ass_time(0), ass_time(duration)

    def rot(x: float, y: float) -> tuple[float, float]:  # same rotation as \frz around the text centre
        a = math.radians(-ang)
        dx, dy = x - 540, y - cy
        return 540 + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)

    def spark(cx: float, cy_: float, angles: list[int]) -> str:  # 3 tapered strokes radiating from a point
        out = []
        for deg in angles:
            r = math.radians(deg)
            x0, y_0 = cx + 18 * math.cos(r), cy_ + 18 * math.sin(r)
            x1, y_1 = cx + 52 * math.cos(r), cy_ + 52 * math.sin(r)
            px, py = -math.sin(r) * 4, math.cos(r) * 4
            out.append(f"m {x0 + px:.0f} {y_0 + py:.0f} l {x1:.0f} {y_1:.0f} l {x0 - px:.0f} {y_0 - py:.0f}")
        return " ".join(out)

    draw = f"\\1c{POP_YELLOW}\\bord2\\3c&H00000000&\\shad0\\p1"
    body = "\\N".join(accent_words(line, accent, POP_YELLOW) for line in lines)
    ev = [f"Dialogue: 4,{t0},{t1},Pop,,0,0,0,,{{\\an8\\pos(540,{y0})\\fs{fs}\\frz{ang}\\org(540,{cy:.0f})}}{body}"]
    width = max(len(line) for line in lines) * fs * 0.43  # approximate text width (Montserrat Black)
    sl = min(width * 0.85, 640)
    sx, sy = rot(540, y0 + n * lh + 10)
    ev.append(f"Dialogue: 3,{t0},{t1},Draw,,0,0,0,,{{\\an7\\pos({sx - sl / 2:.0f},{sy:.0f})\\frz{ang}{draw}}}"
              f"m 0 10 b {sl * .3:.0f} 0 {sl * .7:.0f} -4 {sl:.0f} 0 l {sl:.0f} 7 b {sl * .7:.0f} 5 {sl * .3:.0f} 10 0 20{{\\p0}}")
    lx, ly = rot(540 - len(lines[0]) * fs * 0.43 / 2 - 22, y0 + lh * 0.5)
    rx, ry = rot(540 + len(lines[-1]) * fs * 0.43 / 2 + 22, y0 + lh * (n - 0.5))
    ev.append(f"Dialogue: 3,{t0},{t1},Draw,,0,0,0,,{{\\an7\\pos(0,0){draw}}}"
              f"{spark(lx, ly, [180, 215, 145])} {spark(rx, ry, [0, 35, -35])}{{\\p0}}")
    hy = y0 + n * lh + 58
    if watermark:
        ev.append(f"Dialogue: 2,{t0},{t1},PopHandle,,0,0,0,,{{\\an8\\pos(540,{hy:.0f})}}{ass_escape(watermark)}")
        hl = 260
        ev.append(f"Dialogue: 2,{t0},{t1},Draw,,0,0,0,,{{\\an7\\pos({540 - hl / 2:.0f},{hy + 50:.0f})"
                  f"\\1c{POP_YELLOW}\\bord0\\shad0\\p1}}m 0 6 b 78 0 182 -2 {hl} 0 l {hl} 4 b 182 3 78 7 0 11{{\\p0}}")
    if credit:
        ev.append(f"Dialogue: 1,{t0},{t1},PopCredit,,0,0,0,,{{\\an8\\pos(540,{hy + 66:.0f})}}{ass_escape(credit)}")
    return ev


def build_clip_ass(words: list[dict], hook: str, hook_seconds: float, watermark: str, credit: str,
                   duration: float, layout: dict, words_per_caption: int = 3, top_text: str = "",
                   top_accent: str = "", top_style: str = "pop") -> str:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{CLIP_ASS_STYLES.format(**layout)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if top_text and top_style == "pop":  # tilted meme line + handle + credit, all above the clip
        hook = ""
        lines += pop_top_events(top_text, top_accent, watermark, credit, duration)
        watermark = credit = ""
    elif top_text:  # meme-style line that frames the clip for its whole length (replaces the hook)
        hook = ""
        wrapped = "\\N".join(accent_words(line, top_accent) for line in balanced_wrap(ass_escape(top_text), 20))
        lines.append(f"Dialogue: 3,{ass_time(0)},{ass_time(duration)},Top,,0,0,0,,{wrapped}")
    hook_end = min(hook_seconds, duration)
    if hook:
        wrapped = "\\N".join(textwrap.wrap(ass_escape(hook).upper(), width=18))
        lines.append(f"Dialogue: 3,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,{{\\fad(0,250)}}{wrapped}")
    # captions: groups of N words, never mixing our voice-over with the clip's speech (word "src");
    # one event per word so the word being spoken is highlighted
    groups = []
    for w in words:
        if groups and len(groups[-1]) < words_per_caption and groups[-1][-1].get("src") == w.get("src"):
            groups[-1].append(w)
        else:
            groups.append([w])
    for g, chunk in enumerate(groups):
        group_end = groups[g + 1][0]["start"] if g + 1 < len(groups) else chunk[-1]["end"] + 0.3
        group_end = min(group_end, duration)
        texts = [ass_escape(w["word"]).upper() for w in chunk]
        for j, w in enumerate(chunk):
            start = chunk[0]["start"] if j == 0 else w["start"]
            end = chunk[j + 1]["start"] if j + 1 < len(chunk) else group_end
            if end <= start:
                continue
            shown = " ".join(f"{{\\c{HIGHLIGHT}}}{t}{{\\c&H00FFFFFF&}}" if k == j else t for k, t in enumerate(texts))
            lines.append(f"Dialogue: 2,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{shown}")
    if watermark:  # takes the hook's place once the hook is gone
        lines.append(f"Dialogue: 1,{ass_time(hook_end if hook else 0)},{ass_time(duration)},Watermark,,0,0,0,,"
                     f"{{\\fad(250,0)}}{ass_escape(watermark)}")
    if credit:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(duration)},Credit,,0,0,0,,{ass_escape(credit)}")
    return header + "\n".join(lines) + "\n"


def script_words(text: str, timed: list[dict], duration: float) -> list[dict]:
    """Captions for our own voice-over: the words of the script we wrote (whisper may misspell names),
    timed with whisper's word timings when the counts match, else spread evenly over the voice."""
    words = text.split()
    if not words:
        return []
    if len(timed) == len(words):
        return [{"start": t["start"], "end": t["end"], "word": w, "src": "voice"} for w, t in zip(words, timed)]
    t0 = timed[0]["start"] if timed else 0.0
    t1 = timed[-1]["end"] if timed else duration
    step = max(t1 - t0, 0.1) / len(words)
    return [{"start": round(t0 + i * step, 2), "end": round(t0 + (i + 1) * step, 2), "word": w, "src": "voice"}
            for i, w in enumerate(words)]


def render_clip(job_dir: Path, params: dict, job_id: str) -> dict:
    clip = params["clip"]
    url = clip.get("url")
    if not url:
        raise ValueError("clip.url is required")
    start = float(clip.get("start", 0))
    end = min(float(clip["end"]), start + MAX_CLIP_SECONDS)
    src = fetch_section(url, start, end, job_dir / "clip_src.mp4")
    src_dur = probe_duration(src)
    sw, sh = video_size(src)
    audio_ok = has_audio(src)

    # 1. our voice-over (optional), played over the first seconds of the moving clip
    intro = params.get("intro") or {}
    voice_s = 0.0
    if intro.get("text"):
        synth(intro["text"], job_dir / "intro_voice.wav", float(intro.get("speed", 1.0)), intro.get("voice"),
              intro.get("engine"))
        voice_s = probe_duration(job_dir / "intro_voice.wav")
    duration = round(max(src_dur, voice_s + 0.6), 3)
    fade = min(0.4, duration / 10)
    duck = float(params.get("duck", 0.22))

    # 2. captions: our voice (clean track) + original speech once the voice is over.
    #    captions="intro_only" for clips that already carry the streamer's own burned-in subtitles.
    words = []
    captions = params.get("captions", True)
    if captions:
        if voice_s:
            _, timed, _ = whisper_words(job_dir / "intro_voice.wav", "en")
            words = script_words(intro["text"], timed, voice_s)
        if audio_ok and captions != "intro_only":
            run(["ffmpeg", "-y", "-v", "error", "-i", "clip_src.mp4", "-vn", "-ac", "1", "-ar", "16000",
                 "orig16k.wav"], cwd=job_dir)
            _, orig_words, _ = whisper_words(job_dir / "orig16k.wav", "en")
            words += [{**w, "src": "clip"} for w in orig_words if w["start"] >= voice_s - 0.1]
    # 3. video: blurred background + enlarged foreground with slow zoom and optional punch zoom
    fg_scale = min(max(float(params.get("fg_scale", 1.7)), 1.0), 2.0)
    if sh >= sw:  # vertical source: fill the frame
        fg_w, fg_h = W, H
        prep = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    else:
        fg_h = min(even(W * fg_scale * sh / sw), H)
        fg_w = W
        prep = f"scale={even(W * fg_scale)}:{fg_h},crop={W}:{fg_h}"
    hook_seconds = float(params.get("hook_seconds") or 2.5)
    (job_dir / "subs.ass").write_text(build_clip_ass(
        words, params.get("hook", ""), hook_seconds, params.get("watermark", ""), params.get("credit", ""),
        duration, clip_layout(fg_h), int(params.get("words_per_caption", 3)), params.get("top_text", ""),
        params.get("top_accent", ""), params.get("top_style", "pop")))

    t = f"(on/{FPS})"
    zoom = f"1+0.06*{t}/{duration:.3f}"
    emph = params.get("emphasis_at")
    if emph is not None and 0.3 <= float(emph) <= duration - 0.3:
        zoom += f"+0.22*exp(-pow(({t}-{float(emph):.2f})/0.45,2))"
    pad = max(duration - src_dur, 0)
    vf = (f"[0:v]fps={FPS},setsar=1,split=2[a][b];"
          # background blurred at 1/4 size then upscaled: same look, a fraction of the CPU
          f"[a]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},boxblur=10:1,"
          f"scale={W}:{H},setsar=1[bg];"
          f"[b]{prep},zoompan=z='{zoom}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={fg_w}x{fg_h}:fps={FPS},setsar=1[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,tpad=stop_mode=clone:stop_duration={pad:.3f},"
          f"ass=subs.ass,fade=t=out:st={duration - fade:.3f}:d={fade:.3f},format=yuv420p[v]")

    # 4. audio: original ducked under the voice-over, then back to full volume; fade out at the end
    inputs = ["-i", "clip_src.mp4"]
    if audio_ok:
        orig = "[0:a]aresample=48000,aformat=channel_layouts=stereo"
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        orig = "[1:a]anull"
    if voice_s:
        vi = len(inputs) // 2
        inputs += ["-i", "intro_voice.wav"]
        af = (f"{orig},volume='{duck}+(1-{duck})*min(max((t-{voice_s:.2f})/0.4,0),1)':eval=frame,apad[o];"
              f"[{vi}:a]aresample=48000,aformat=channel_layouts=stereo,volume=1.5[vo];"
              f"[o][vo]amix=inputs=2:duration=first:normalize=0,")
    else:
        af = f"{orig},apad,"
    # loudness normalised to the usual short-video level, true peak capped (no clipping)
    af += (f"atrim=0:{duration:.3f},loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
           f"afade=t=out:st={duration - fade:.3f}:d={fade:.3f}[aout]")

    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[aout]",
         "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS),
         "-threads", "0",
         *AUDIO_OUT, "-movflags", "+faststart", "reel.mp4"], cwd=job_dir)
    return finish(job_dir, job_id, duration, len(words),
                  extra={"clip_start": start, "clip_end": end, "intro_seconds": round(voice_s, 2),
                         "emphasis_at": emph})


PROCESSORS = {"tts": process_tts, "transcribe": process_transcribe, "render": process_render}
