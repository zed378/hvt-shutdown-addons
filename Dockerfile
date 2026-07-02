# ---- Stage 1: Build UI extension ----
FROM node:24-alpine AS ui-builder
WORKDIR /build
# Install bash (required by @rancher/shell build scripts)
RUN apk add --no-cache bash
COPY hvt-shutdown-ui/ .
RUN yarn install --frozen-lockfile 2>/dev/null || npm install
# Build the UI package; version is read from package.json automatically
RUN yarn build-pkg hvt-shutdown-ui true

# ---- Stage 2: Python runtime with static files ----
FROM python:3.11-slim
WORKDIR /app

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
RUN apt-get update && apt-get install -y --no-install-recommends \
    coreutils \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy API app
COPY app/ ./app

# Copy static UI files from builder stage
RUN mkdir -p /app/static/ui-plugin/plugin

# Copy package.json into plugin/ subdirectory (use wildcard for versioned dir)
COPY --from=ui-builder /build/dist-pkg/hvt-shutdown-ui-*/package.json /app/static/ui-plugin/plugin/package.json

# Copy all built JS bundles (*.umd.min.js excludes .map files)
COPY --from=ui-builder /build/dist-pkg/hvt-shutdown-ui-*/hvt-shutdown-ui-*.umd.min.js /app/static/ui-plugin/

# Generate files.txt manifest dynamically from built files
RUN (echo "plugin/package.json" && find /app/static/ui-plugin -maxdepth 1 -name "*.umd.min.js" -printf "%f\n") \
    > /app/static/ui-plugin/files.txt

RUN chown -R appuser:appuser /app
EXPOSE 8080
EXPOSE 8081

# Run as root for host shutdown capability (privileged mode handles security)
USER root

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
