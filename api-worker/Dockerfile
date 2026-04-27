FROM python:3.11-slim

RUN useradd -m -u 1000 -s /bin/bash api-worker

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R api-worker:api-worker /app
USER api-worker

CMD ["python", "main.py"]
