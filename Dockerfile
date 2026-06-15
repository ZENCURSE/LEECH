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
    && rm -rf /var/lib/apt/lists/*

# Download JDownloader2
RUN mkdir -p /JDownloader && \
    wget -q "https://installer.jdownloader.org/JDownloader.jar" \
    -O /JDownloader/JDownloader.jar && \
    echo "JDownloader downloaded"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /downloads /app/data /app/data/thumbs /app/data/cookies /JDownloader/cfg /JDownloader/logs

EXPOSE 8000 3128

CMD ["python", "main.py"]
