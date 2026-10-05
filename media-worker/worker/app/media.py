"""Media processors: TTS (Piper), transcription (faster-whisper), Reel rendering (FFmpeg).

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
DATA_DIR = Path(os.environ.get("DATA_DIR", "/srv/media"))

_whisper = None
_whisper_lock = threading.Lock()


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
def synth(text: str, out: Path, speed: float = 1.0) -> Path:
    text = " ".join(text.split())
    if not text:
        raise ValueError("voiceover text is empty")
    if len(text) > 3000:
        raise ValueError("voiceover text longer than 3000 characters")
    # Piper: length_scale < 1 = faster speech
    run(["piper", "--model", PIPER_VOICE, "--output_file", str(out), "--length_scale", f"{1 / max(speed, 0.5):.3f}"],
        stdin=text)
    return out


def process_tts(job_dir: Path, params: dict, job_id: str) -> dict:
    out = synth(params.get("text", ""), job_dir / "voice.wav", float(params.get("speed", 1.0)))
    return {"audio_url": public_url(job_id, out.name), "duration": round(probe_duration(out), 2)}


# ---------------------------------------------------------------- transcription
def process_transcribe(job_dir: Path, params: dict, job_id: str) -> dict:
    url = params.get("media_url")
    if not url:
        raise ValueError("media_url is required")
    src = download(url, job_dir / "source")
    audio = job_dir / "audio.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(audio)])
    segs, words, duration = whisper_words(audio, params.get("language", "en"))
    payload = {"duration": duration, "segments": segs, "words": words}
    (job_dir / "transcript.json").write_text(json.dumps(payload, ensure_ascii=False))
    src.unlink(missing_ok=True)
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
              words_per_caption: int = 3) -> str:
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
    max_duration = float(params.get("max_duration", 90))
    visuals = params.get("visuals") or [{"type": "color", "color": "#111111"}]

    # 1. audio: TTS voiceover or provided audio file
    vo = params.get("voiceover") or {}
    voice = job_dir / "voice.wav"
    if vo.get("text"):
        synth(vo["text"], voice, float(vo.get("speed", 1.0)))
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
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, duration / 2):.2f}", "-i", "reel.mp4", "-frames:v", "1",
         "-q:v", "3", "cover.jpg"], cwd=job_dir)

    for p in job_dir.iterdir():  # keep only deliverables
        if p.name not in ("reel.mp4", "cover.jpg", "subs.ass", "voice.wav"):
            p.unlink(missing_ok=True)

    reel = job_dir / "reel.mp4"
    return {
        "video_url": public_url(job_id, "reel.mp4"),
        "cover_url": public_url(job_id, "cover.jpg"),
        "duration": round(probe_duration(reel), 2),
        "width": W,
        "height": H,
        "size_bytes": reel.stat().st_size,
        "captions": len(words),
    }


PROCESSORS = {"tts": process_tts, "transcribe": process_transcribe, "render": process_render}
