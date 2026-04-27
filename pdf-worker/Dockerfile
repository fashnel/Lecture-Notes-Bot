FROM python:3.11-slim

RUN useradd -m -u 1000 -s /bin/bash pdf-worker

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    fonts-dejavu-core \
    fonts-noto-core \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R pdf-worker:pdf-worker /app
USER pdf-worker

CMD ["python", "main.py"]
