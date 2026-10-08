FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg build-essential \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY narjo_sing ./narjo_sing
COPY third_party ./third_party
# The diffq stand-in goes in first so audio-separator's diffq requirement never pulls the real (non-commercial) one.
RUN pip install --no-cache-dir ./third_party/diffq_stub \
 && pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir ".[separator]" \
 && pip show diffq | grep -q 'narjo.stub'
ENV SING_MUSIC_DIR=/music SING_MODELS_DIR=/models SING_STEMS_DIR=/stems PYTHONUNBUFFERED=1
VOLUME ["/models", "/stems"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/v1/ping', timeout=4)"
ENTRYPOINT ["python", "-m", "narjo_sing"]
