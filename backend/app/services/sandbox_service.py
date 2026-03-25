"""Sandbox Service — Docker-based isolated code execution for agents.

Runs agent-generated code in ephemeral Docker containers with strict limits:
- 30s timeout
- 256MB memory
- No network access
- Read-only filesystem (except /tmp)
- One container per execution (create → run → destroy)
"""

import asyncio
import logging
from dataclasses import dataclass

import docker
from docker.errors import ImageNotFound, APIError

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = "ai-holding-sandbox:latest"
SANDBOX_FALLBACK_IMAGE = "python:3.12-slim"
MEMORY_LIMIT = "256m"
CPU_PERIOD = 100_000
CPU_QUOTA = 50_000  # 50% of one CPU
EXECUTION_TIMEOUT = 30  # seconds
MAX_OUTPUT_LENGTH = 10_000  # chars

SUPPORTED_LANGUAGES = {"python"}


@dataclass
class ExecutionResult:
    success: bool
    output: str
    error: str
    language: str
    exit_code: int
    timed_out: bool


class SandboxService:
    """Execute code in isolated Docker containers."""

    def __init__(self):
        self._client: docker.DockerClient | None = None
        self._image_available = False

    def _get_client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    async def _ensure_image(self):
        """Check sandbox image exists, fall back to python:3.12-slim."""
        if self._image_available:
            return

        client = self._get_client()
        try:
            client.images.get(SANDBOX_IMAGE)
            self._image_available = True
            logger.info(f"Sandbox image '{SANDBOX_IMAGE}' ready")
        except ImageNotFound:
            logger.info(f"Sandbox image not found, pulling '{SANDBOX_FALLBACK_IMAGE}'...")
            try:
                client.images.pull(SANDBOX_FALLBACK_IMAGE)
                self._image_available = True
                logger.info(f"Fallback image '{SANDBOX_FALLBACK_IMAGE}' ready")
            except APIError as e:
                logger.error(f"Failed to pull sandbox image: {e}")
                raise RuntimeError("No sandbox image available") from e

    def _get_image_name(self) -> str:
        """Return whichever sandbox image is available."""
        client = self._get_client()
        try:
            client.images.get(SANDBOX_IMAGE)
            return SANDBOX_IMAGE
        except ImageNotFound:
            return SANDBOX_FALLBACK_IMAGE

    async def execute(
        self, code: str, language: str = "python", network_enabled: bool = False,
    ) -> ExecutionResult:
        """Execute code in a sandboxed Docker container.

        Args:
            code: The code to execute.
            language: Programming language (currently only 'python').
            network_enabled: If True, allow network access for API calls.

        Returns ExecutionResult with output, errors, and metadata.
        """
        if language not in SUPPORTED_LANGUAGES:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Unsupported language: {language}. Supported: {', '.join(SUPPORTED_LANGUAGES)}",
                language=language,
                exit_code=-1,
                timed_out=False,
            )

        await self._ensure_image()

        # Run container in a thread to avoid blocking
        return await asyncio.to_thread(self._run_container, code, language, network_enabled)

    def _run_container(self, code: str, language: str, network_enabled: bool = False) -> ExecutionResult:
        """Synchronous container execution (called from thread)."""
        client = self._get_client()
        image = self._get_image_name()
        container = None

        try:
            # Create container with strict security limits
            container = client.containers.create(
                image=image,
                command=["python3", "-c", code],
                mem_limit=MEMORY_LIMIT,
                cpu_period=CPU_PERIOD,
                cpu_quota=CPU_QUOTA,
                network_disabled=not network_enabled,
                read_only=True,
                tmpfs={"/tmp": "size=64m,noexec"},
                security_opt=["no-new-privileges"],
                user="nobody",
                working_dir="/tmp",
                detach=True,
            )

            container.start()

            # Wait with timeout
            result = container.wait(timeout=EXECUTION_TIMEOUT)
            exit_code = result.get("StatusCode", -1)

            # Capture output
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            # Truncate output
            stdout = stdout[:MAX_OUTPUT_LENGTH]
            stderr = stderr[:MAX_OUTPUT_LENGTH]

            return ExecutionResult(
                success=(exit_code == 0),
                output=stdout,
                error=stderr if exit_code != 0 else "",
                language=language,
                exit_code=exit_code,
                timed_out=False,
            )

        except Exception as e:
            error_msg = str(e)
            timed_out = "timed out" in error_msg.lower() or "read timeout" in error_msg.lower()

            if timed_out:
                # Kill the container if it timed out
                try:
                    if container:
                        container.kill()
                except Exception:
                    pass

            return ExecutionResult(
                success=False,
                output="",
                error=f"{'Execution timed out (30s limit)' if timed_out else error_msg}",
                language=language,
                exit_code=-1,
                timed_out=timed_out,
            )

        finally:
            # Always clean up the container
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    @property
    def is_available(self) -> bool:
        """Check if Docker is available."""
        try:
            client = self._get_client()
            client.ping()
            return True
        except Exception:
            return False


# Singleton
sandbox_service = SandboxService()
