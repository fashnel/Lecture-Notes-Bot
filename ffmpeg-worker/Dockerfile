FROM python:3.11-slim

RUN useradd -m -u 1000 -s /bin/bash ffmpeg-worker

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R ffmpeg-worker:ffmpeg-worker /app
USER ffmpeg-worker

CMD ["python", "main.py"]