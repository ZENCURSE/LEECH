FROM python:3.10-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    unrar-free \
    ffmpeg \
    libmagic1 \
    curl \
    wget \
    default-jre-headless \
    cpulimit \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# JDownloader — download jar at build time so it's baked into the image
RUN mkdir -p /JDownloader/cfg /JDownloader/logs && \
    wget -q --show-progress --progress=bar:force \
         -O /JDownloader/JDownloader.jar \
         https://installer.jdownloader.org/JDownloader.jar && \
    echo "[JD] JDownloader.jar downloaded ($(du -sh /JDownloader/JDownloader.jar | cut -f1))"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /downloads /app/data /app/data/thumbs /app/data/cookies /JDownloader/cfg /JDownloader/logs

EXPOSE 8000 3128

CMD ["python", "main.py"]
