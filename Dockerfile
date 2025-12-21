FROM alpine:3.23

LABEL maintainer="advBBS Project"
LABEL description="Lightweight BBS for Meshtastic Mesh Networks"
LABEL version="0.1.0"

ARG UID=1000
ARG GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install System Deps + Pre-compiled Python Libraries
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-cryptography \
    py3-argon2-cffi \
    ttyd \
    udev \
    bash \
    curl \
    procps \
    dialog

# Create non-root user
RUN apk add --no-cache shadow && \
    groupadd -g ${GID} advbbs && \
    useradd -u ${UID} -g ${GID} -m -s /bin/bash advbbs && \
    addgroup advbbs dialout && \
    apk del shadow

# Create directories
RUN mkdir -p /app /data /data/backups /var/log && \
    chown -R advbbs:advbbs /app /data /var/log

WORKDIR /app

COPY requirements.txt .

# Install remaining Python deps (meshtastic, rich, tomli)
# We use --break-system-packages because we are intentionally mixing 
# apk packages (cryptography) with pip packages in this container.
RUN apk add --no-cache --virtual .build-deps build-base linux-headers python3-dev libffi-dev && \
    pip install --no-cache-dir --break-system-packages -r requirements.txt && \
    apk del .build-deps

COPY --chown=advbbs:advbbs advbbs/ /app/advbbs/
COPY --chown=advbbs:advbbs config.example.toml /app/
COPY --chown=advbbs:advbbs docker-entrypoint.sh /app/
COPY --chown=advbbs:advbbs advbbs-config /usr/local/bin/advbbs-config

USER advbbs

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=3 \
    CMD python3 -c "import sqlite3; sqlite3.connect('/data/advbbs.db').execute('SELECT 1')" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
