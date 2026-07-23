# Pi-side app only. The Jetson service is meant to run natively (see
# jetson_service/ and the README) -- GPU passthrough for Ollama under Docker
# on Jetson's L4T needs NVIDIA-specific base images and isn't worth the
# complexity yet for a single-box hobby setup.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY flipfinder/ flipfinder/

# config.yaml, .env, and data/ are mounted in via docker-compose.yml, not
# baked into the image -- see .dockerignore.
ENTRYPOINT ["python", "-m", "flipfinder.main"]
