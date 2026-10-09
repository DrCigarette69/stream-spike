# Build context = repo root (docker-compose.yml: context ".", dockerfile control/Dockerfile).
# Only control/ and the single-source A3.0 guard enter the context (control/Dockerfile.dockerignore).
FROM python:3.12-slim
WORKDIR /app
COPY control/ .
# A3.3 iroh_local minting imports spike_private_guard; same file as the host harness uses.
COPY scripts/spike_private_guard.py ./spike_private_guard.py
CMD ["python", "-u", "main.py"]
