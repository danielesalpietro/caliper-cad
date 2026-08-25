FROM python:3.11-slim

# [Prompt to Part] libgl1 + cadquery: /api/generate converte lo STEP
# generato in STL per il viewer 3D della pagina statica — stessa
# necessita' di services/verifier/executor/Dockerfile, duplicata qui
# perche' e' l'unico altro punto che deve farlo (dashboard e
# verifier-executor sono processi/reti separati per scelta, vedi
# network_mode: none su verifier-executor).
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --default-timeout=300 cadquery==2.8.0

COPY app.py .
COPY static ./static

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
