# ---- Python runtime with FastAPI ----
# UI extension static files are served from GitHub Pages, NOT from this image.
# See: https://zed378.github.io/hvt-shutdown-addons
FROM python:3.11-slim

WORKDIR /app

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy API app
COPY app/ ./app

# Copy scripts into the container for runtime use
RUN mkdir -p /app/scripts
COPY scripts/init.sh /app/scripts/init.sh
RUN chmod +x /app/scripts/init.sh

RUN chown -R appuser:appuser /app

EXPOSE 8080

# Run as root for host shutdown capability (privileged mode handles security)
USER root

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]