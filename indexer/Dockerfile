FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY indexer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY indexer /app/indexer
COPY archive_backup.py archive_io.py /app/
ENV FONT_INDEXER_DATA=/data FONT_INDEXER_HOST=0.0.0.0
CMD ["python", "-m", "indexer.service"]
