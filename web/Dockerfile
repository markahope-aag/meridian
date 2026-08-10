FROM python:3.11-slim

RUN apt-get update -qq && apt-get install -y -qq \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libffi-dev libcairo2 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy into /app/web/, not /app/, so the package-form imports in app.py
# (from web.config, from web.helpers, from web.registry) resolve. This matches
# the layout scripts/vm-auto-deploy.sh produces. Flattening into /app/ builds
# an image that fails at import time with "No module named 'web'".
COPY . /app/web/

ENV MERIDIAN_ROOT=/meridian
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/')"

# web.app:app, not app:app. Gunicorn puts its working directory (/app) on
# sys.path, and web/__init__.py makes /app/web an importable package.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "web.app:app"]
