"""Tests for the sandbox service."""

from unittest.mock import patch, AsyncMock

import pytest

from app.services.sandbox_service import SandboxService, ExecutionResult


class TestExecutionResult:
    def test_successful_result(self):
        r = ExecutionResult(
            success=True,
            output="4\n",
            error="",
            language="python",
            exit_code=0,
            timed_out=False,
        )
        assert r.success
        assert r.output == "4\n"
        assert not r.timed_out

    def test_failed_result(self):
        r = ExecutionResult(
            success=False,
            output="",
            error="NameError: name 'x' is not defined",
            language="python",
            exit_code=1,
            timed_out=False,
        )
        assert not r.success
        assert "NameError" in r.error


class TestSandboxService:
    @pytest.mark.asyncio
    async def test_unsupported_language(self):
        svc = SandboxService()
        result = await svc.execute("console.log('hi')", language="javascript")
        assert not result.success
        assert "Unsupported language" in result.error

    @pytest.mark.asyncio
    async def test_execute_mocked(self):
        svc = SandboxService()
        mock_result = ExecutionResult(
            success=True, output="4\n", error="",
            language="python", exit_code=0, timed_out=False,
        )
        with patch.object(svc, "_run_container", return_value=mock_result):
            with patch.object(svc, "_ensure_image", new_callable=AsyncMock):
                result = await svc.execute("print(2+2)")
                assert result.success
                assert result.output == "4\n"

    def test_is_available(self):
        svc = SandboxService()
        # Just check it doesn't crash
        available = svc.is_available
        assert isinstance(available, bool)
