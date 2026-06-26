FROM python:3.13-slim

# Build as: docker build -t memory-service:1.6.0 .
LABEL org.opencontainers.image.version="1.6.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMORY_SERVICE_DATA_DIR=/data

WORKDIR /opt/memory-service

RUN groupadd --system memorysvc \
    && useradd --system --gid memorysvc --no-create-home --home-dir /nonexistent memorysvc \
    && mkdir -p /opt/memory-service /data \
    && chown -R memorysvc:memorysvc /opt/memory-service /data

COPY app/memory_service ./memory_service
COPY app/run_production_stdio_server.py ./run_production_stdio_server.py

VOLUME ["/data"]

USER memorysvc

ENTRYPOINT ["python", "run_production_stdio_server.py"]