FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim
ARG DRCLOUD_BUILD_COMMIT=unknown
ARG DRCLOUD_BUILD_DATE=unknown
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DRCLOUD_ENV=production DRCLOUD_DATA_DIR=/data BARCODE_SYNC_MODE=dry-run DRCLOUD_SAFE_MODE=true HOST=0.0.0.0 PORT=8080 \
    DRCLOUD_ROADMAP=/app/config/roadmap_v3.json DRCLOUD_BUILD_COMMIT=${DRCLOUD_BUILD_COMMIT} DRCLOUD_BUILD_DATE=${DRCLOUD_BUILD_DATE}
RUN addgroup --system drcloud && adduser --system --ingroup drcloud --home /app drcloud && mkdir /data && chown drcloud:drcloud /data
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY --chown=drcloud:drcloud config/roadmap_v3.json /app/config/roadmap_v3.json
USER drcloud
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2)"
CMD ["dr-cloud-sync", "os-serve"]
