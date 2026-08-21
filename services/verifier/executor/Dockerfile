FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --default-timeout=300 cadquery==2.8.0

WORKDIR /app
COPY run_and_measure.py watcher.py gauge_check.py .

CMD ["python", "watcher.py"]
