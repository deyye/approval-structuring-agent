FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV HOST=0.0.0.0 PORT=8765 DATA_DIR=/app/data
EXPOSE 8765
CMD ["python", "-m", "app.server"]
