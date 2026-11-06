FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY admin ./admin
COPY data ./data
COPY pyproject.toml ./
RUN useradd --create-home appuser && mkdir -p /app/instance /app/uploads && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
