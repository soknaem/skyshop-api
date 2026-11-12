# One image recipe for all 7 services. Build with: --build-arg SERVICE_DIR=auth_service (etc.)
FROM python:3.12-slim

ARG SERVICE_DIR
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Layout inside the container: /app/<service_dir>/<service>_app/...
# (the code expects the service folder to sit one level below the project root)
WORKDIR /app/${SERVICE_DIR}

COPY ${SERVICE_DIR}/requirements.txt .
RUN pip install -r requirements.txt

COPY ${SERVICE_DIR}/ .

# writable dirs used at runtime: auth RSA keys, media uploads
RUN useradd --system --uid 10001 --home /app app \
 && mkdir -p keys storage \
 && chown -R app:app /app
USER app