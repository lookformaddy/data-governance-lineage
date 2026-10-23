FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
COPY examples ./examples

RUN pip install --upgrade pip && pip install -r requirements.txt && pip install -e .

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Default: run the API service. For the offline demo instead:
#   docker run --rm <image> data-governance-lineage demo
CMD ["data-governance-lineage", "serve", "--host", "0.0.0.0", "--port", "8000"]
