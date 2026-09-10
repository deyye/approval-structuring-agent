FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
RUN useradd --uid 10001 --create-home agent && mkdir /app/data && chown agent:agent /app/data
COPY app ./app
COPY scripts/check_model.py ./scripts/check_model.py
ENV HOST=0.0.0.0 PORT=8765 DATA_DIR=/app/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2).read()"
CMD ["python", "-m", "app.server"]
