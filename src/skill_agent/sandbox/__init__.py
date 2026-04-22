from .docker_runner import DockerSandboxRunner
from .local_runner import LocalSandboxRunner

# Backward-compatible alias — existing code that imports SandboxRunner still works.
SandboxRunner = LocalSandboxRunner

__all__ = ["SandboxRunner", "LocalSandboxRunner", "DockerSandboxRunner"]
