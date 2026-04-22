# Sandbox image for skill-agent skill execution.
#
# Build:
#   docker build -f docker/Dockerfile -t skill-agent-sandbox:latest .
#
# The runner mounts a skill workspace into /workspace and calls:
#   python scripts/run.py
#
# Skills are expected to use only the Python standard library.
# Add pip install lines below if generated skills require third-party packages.

FROM python:3.12-slim

# Drop to a non-root user so skill scripts cannot modify system files.
RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox

WORKDIR /workspace

# No ENTRYPOINT — the runner specifies the full command:
#   docker run ... skill-agent-sandbox:latest python scripts/run.py
