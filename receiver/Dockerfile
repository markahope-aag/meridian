FROM python:3.11-slim

RUN groupadd -r meridian && useradd -r -g meridian -m meridian

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app.py is read from the bind mount at /meridian/receiver/app.py.
# This means endpoint changes take effect without rebuilding the image.
# Gunicorn points at the bind-mounted copy.
ENV MERIDIAN_ROOT=/meridian
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

USER meridian

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "--chdir", "/meridian/receiver", "app:app"]
