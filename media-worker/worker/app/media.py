"""Media processors: TTS (Kokoro, Piper fallback), transcription (faster-whisper), Reel rendering (FFmpeg).

Each processor receives (job_dir, params, job_id) and returns a JSON-serialisable result.
Inputs are only fetched from plain http(s) URLs: the worker never scrapes platforms.
Whether a source may be used is decided upstream (rights_status in n8n), not here.
"""

import json
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
SOURCE_HOSTS = [h.strip() for h in os.environ.get("SOURCE_HOSTS", "youtube.com,youtu.be").split(",") if h.strip()]
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


def fetch_section(url: str, start: float, end: float, out: Path) -> Path:
    """Only the [start, end] section of a video, with audio (mp4)."""
    if end <= start:
        raise ValueError("clip end must be greater than start")
    if is_platform_url(url):
        tmp = out.with_name("section_src")
        run(["yt-dlp", "--no-playlist", "--no-progress",
             "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
             "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
             "--merge-output-format", "mp4", "-o", str(tmp) + ".%(ext)s", url])
        src = next(out.parent.glob("section_src.*"))
        src.rename(out)
        return out
    src = download(url, out.with_name("section_full"))
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(out)])
    src.unlink(missing_ok=True)
    return out


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
# Source video fitted in the 9:16 frame over a blurred copy of itself (no faces cropped out).
FIT_BLUR = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:2,setsar=1[bg];"
            f"[0:v]scale={W}:-2,setsar=1[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},format=yuv420p[v]")
AUDIO_OUT = ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]


def has_audio(path: Path) -> bool:
    out = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
               str(path)])
    return bool(out.strip())


def render_clip(job_dir: Path, params: dict, job_id: str) -> dict:
    clip = params["clip"]
    url = clip.get("url")
    if not url:
        raise ValueError("clip.url is required")
    start = float(clip.get("start", 0))
    end = min(float(clip["end"]), start + MAX_CLIP_SECONDS)
    fetch_section(url, start, end, job_dir / "clip_src.mp4")

    # 1. normalise the clip: 1080x1920 blur layout, 30 fps, AAC 48 kHz stereo (silent track if none)
    audio_in = ["-map", "0:a:0"] if has_audio(job_dir / "clip_src.mp4") else ["-map", "1:a"]
    run(["ffmpeg", "-y", "-v", "error", "-i", "clip_src.mp4", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-filter_complex", FIT_BLUR, "-map", "[v]", *audio_in, "-shortest",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", *AUDIO_OUT, "clip.mp4"], cwd=job_dir)

    # 2. optional intro: our voiceover over the darkened first frame (adds context + originality)
    parts = []
    intro = params.get("intro") or {}
    intro_seconds = 0.0
    if intro.get("text"):
        synth(intro["text"], job_dir / "intro_voice.wav", float(intro.get("speed", 1.0)), intro.get("voice"),
              intro.get("engine"))
        intro_seconds = probe_duration(job_dir / "intro_voice.wav") + 0.25
        run(["ffmpeg", "-y", "-v", "error", "-i", "clip.mp4", "-frames:v", "1", "first.png"], cwd=job_dir)
        run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", "first.png", "-i", "intro_voice.wav",
             "-vf", f"eq=brightness=-0.18:saturation=0.8,fps={FPS},format=yuv420p", "-af", "apad",
             "-t", f"{intro_seconds:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", *AUDIO_OUT,
             "intro.mp4"], cwd=job_dir)
        parts.append("intro.mp4")
    parts.append("clip.mp4")

    if len(parts) == 1:
        (job_dir / "clip.mp4").rename(job_dir / "base.mp4")
    else:
        inputs = sum((["-i", p] for p in parts), [])
        streams = "".join(f"[{i}:v][{i}:a]" for i in range(len(parts)))
        run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex",
             f"{streams}concat=n={len(parts)}:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", *AUDIO_OUT, "base.mp4"], cwd=job_dir)
    duration = probe_duration(job_dir / "base.mp4")

    # 3. captions from the final audio (intro voice + original speech)
    words = []
    if params.get("captions", True):
        run(["ffmpeg", "-y", "-v", "error", "-i", "base.mp4", "-vn", "-ac", "1", "-ar", "16000", "speech.wav"],
            cwd=job_dir)
        _, words, _ = whisper_words(job_dir / "speech.wav", "en")
    hook_seconds = float(params.get("hook_seconds") or max(intro_seconds, 3))
    (job_dir / "subs.ass").write_text(build_ass(
        words, params.get("hook", ""), hook_seconds, params.get("watermark", ""), duration,
        int(params.get("words_per_caption", 3)), params.get("credit", "")))

    # 4. burn subtitles, keep audio
    run(["ffmpeg", "-y", "-v", "error", "-i", "base.mp4", "-vf", "ass=subs.ass", "-c:v", "libx264",
         "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), *AUDIO_OUT,
         "-movflags", "+faststart", "reel.mp4"], cwd=job_dir)
    return finish(job_dir, job_id, duration, len(words),
                  extra={"clip_start": start, "clip_end": end, "intro_seconds": round(intro_seconds, 2)})


PROCESSORS = {"tts": process_tts, "transcribe": process_transcribe, "render": process_render}
