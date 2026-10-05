FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/srv/media \
    HF_HOME=/srv/media/hf \
    PIPER_VOICE=/opt/piper/en_US-lessac-medium.onnx

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Default English voice for Piper (local, free TTS)
RUN mkdir -p /opt/piper \
 && curl -fsSL -o /opt/piper/en_US-lessac-medium.onnx \
    https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
 && curl -fsSL -o /opt/piper/en_US-lessac-medium.onnx.json \
    https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

# Default voice engine: Kokoro (natural, local, Apache-2.0), int8 model for CPU
RUN mkdir -p /opt/kokoro \
 && curl -fsSL -o /opt/kokoro/kokoro-v1.0.int8.onnx \
    https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx \
 && curl -fsSL -o /opt/kokoro/voices-v1.0.bin \
    https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

COPY app ./app

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD curl -fs http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
