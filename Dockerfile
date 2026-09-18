# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MAGAZINE_SORTER_DATA_DIR=/config \
    MAGAZINE_SORTER_INPUT_FOLDER=/input \
    MAGAZINE_SORTER_OUTPUT_FOLDER=/library \
    MAGAZINE_SORTER_OCR_BACKEND=local \
    MAGAZINE_SORTER_TESSDATA_DIR=/usr/share/tesseract-ocr/5/tessdata

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gosu \
        tesseract-ocr \
        tesseract-ocr-dan \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY src ./src

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
