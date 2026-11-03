FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CELLSCRIBE_DB=/data/cellscribe.db

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin cellscribe \
    && mkdir -p /data \
    && chown -R cellscribe:cellscribe /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py store.py admin.html ./

USER cellscribe

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4).status==200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
