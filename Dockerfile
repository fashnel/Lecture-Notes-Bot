FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    xfonts-75dpi \
    wget \
    ca-certificates \
    && wget https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltopdf_0.12.6.1-2.bullseye_amd64.deb \
    && apt-get install -y ./wkhtmltopdf_0.12.6.1-2.bullseye_amd64.deb \
    && rm wkhtmltopdf_0.12.6.1-2.bullseye_amd64.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "main.py"]