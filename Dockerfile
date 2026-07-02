# ---- Stage 1: Build UI extension ----
FROM node:24-alpine AS ui-builder
WORKDIR /build
# Install bash (required by @rancher/shell build scripts)
RUN apk add --no-cache bash
COPY hvt-shutdown-ui/ .
RUN yarn install --frozen-lockfile 2>/dev/null || npm install
# Build the UI package; version is read from package.json automatically
RUN yarn build-pkg hvt-shutdown-ui true

# ---- Stage 2: Python runtime with FastAPI static file serving ----
FROM python:3.11-slim

WORKDIR /app

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy API app
COPY app/ ./app

# Copy UI builder output and place static files for FastAPI to serve on port 8081
RUN mkdir -p /app/static/ui-plugin
COPY --from=ui-builder /build/dist-pkg/ /tmp/ui-dist/
# Copy all files from the built package directory (flexible glob for Rancher build-pkg output)
RUN ls /tmp/ui-dist/ | grep '^hvt-shutdown-ui-' | head -1 | xargs -I{} cp /tmp/ui-dist/{}/package.json /app/static/ui-plugin/
RUN ls /tmp/ui-dist/ | grep '^hvt-shutdown-ui-' | head -1 | xargs -I{} sh -c 'cp "$1"/*.js /app/static/ui-plugin/ 2>/dev/null || cp "$1"/* /app/static/ui-plugin/ 2>/dev/null || true' _

# Copy scripts into the container for runtime use
RUN mkdir -p /app/scripts
COPY scripts/init.sh /app/scripts/init.sh
RUN chmod +x /app/scripts/init.sh

RUN chown -R appuser:appuser /app

EXPOSE 8080
EXPOSE 8081

# Run as root for host shutdown capability (privileged mode handles security)
USER root

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
