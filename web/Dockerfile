FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir flask gunicorn pyyaml requests

COPY . /app/

ENV MERIDIAN_ROOT=/meridian
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/')"

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "app:app"]
