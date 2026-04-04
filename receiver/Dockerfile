FROM python:3.11-slim

RUN groupadd -r meridian && useradd -r -g meridian -m meridian

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# /meridian/ is provided at runtime via Coolify bind mount.
# It contains everything: capture/, raw/, wiki/, agents/, prompts/, config.yaml.
# The receiver only needs its own app.py — all other code is read from the bind mount.
ENV MERIDIAN_ROOT=/meridian
ENV PYTHONUNBUFFERED=1

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

USER meridian

CMD ["gunicorn", "--bind", "0.0.0.0:8090", "--workers", "2", "--timeout", "120", "app:app"]
