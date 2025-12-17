# advBBS Dockerfile
# Lightweight BBS for Meshtastic Mesh Networks
#
# Build: docker build -t advbbs .
# Run:   docker run -d --name advbbs \
#          --device=/dev/ttyUSB0 \
#          -v advbbs_data:/data \
#          -v ./config.toml:/app/config.toml:ro \
#          advbbs

FROM python:3.11-slim-bookworm

# Labels
LABEL maintainer="advBBS Project"
LABEL description="Lightweight BBS for Meshtastic Mesh Networks"
LABEL version="0.1.0"

# Build arguments
ARG UID=1000
ARG GID=1000

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # For serial communication
    udev \
    # For Argon2 (native extension)
    libffi-dev \
    # For health checks
    curl \
    # For process management (restart signal)
    procps \
    && rm -rf /var/lib/apt/lists/* \
    # Install ttyd from GitHub releases
    && curl -sL https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 -o /usr/local/bin/ttyd \
    && chmod +x /usr/local/bin/ttyd

# Create non-root user
RUN groupadd -g ${GID} advbbs && \
    useradd -u ${UID} -g ${GID} -m -s /bin/bash advbbs && \
    # Add to dialout group for serial access
    usermod -aG dialout advbbs

# Create directories
RUN mkdir -p /app /data /data/backups /var/log && \
    chown -R advbbs:advbbs /app /data /var/log

WORKDIR /app

# Install Python dependencies first (for layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=advbbs:advbbs advbbs/ /app/advbbs/
COPY --chown=advbbs:advbbs config.example.toml /app/
COPY --chown=advbbs:advbbs docker-entrypoint.sh /app/
COPY --chown=advbbs:advbbs advbbs-config /usr/local/bin/advbbs-config

# Switch to non-root user
USER advbbs

# Data volume mount point
VOLUME ["/data"]

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sqlite3; sqlite3.connect('/data/advbbs.db').execute('SELECT 1')" || exit 1

# Entrypoint handles config validation
ENTRYPOINT ["/app/docker-entrypoint.sh"]
# No CMD needed - entrypoint handles config path via advBBS_CONFIG env var
