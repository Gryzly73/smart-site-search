FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV SSS_CATALOG_PATH=/catalog/catalog.sqlite SSS_RUNTIME_PATH=/runtime/state.sqlite
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD ["python", "-c", "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=2)); raise SystemExit(not r['ready'])"]
CMD ["uvicorn", "smart_site_search.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
