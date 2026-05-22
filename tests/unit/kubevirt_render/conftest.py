"""Shared fixtures for kubevirt renderer unit tests."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

HARNESS = Path(__file__).parent / "render_harness.yml"
# Note: validate_harness.yml is created in Task 11; the run_validate fixture
# fails clearly if invoked before then.
VALIDATE_HARNESS = Path(__file__).parent / "validate_harness.yml"


def _run_harness(harness: Path, host_spec: dict[str, Any]) -> subprocess.CompletedProcess:
    """Run an ansible-playbook harness with host_spec, return the completed process."""
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "rendered.yml"
        extra_vars = {"host_spec": host_spec, "output_path": str(output_path)}
        proc = subprocess.run(
            [
                "ansible-playbook",
                str(harness),
                "--extra-vars",
                json.dumps(extra_vars),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        proc.output_contents = output_path.read_text() if output_path.exists() else ""  # type: ignore[attr-defined]
        return proc


@pytest.fixture
def render_vm() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return a function that renders a VM from a per-host spec dict."""

    def _render(host_spec: dict[str, Any]) -> dict[str, Any]:
        proc = _run_harness(HARNESS, host_spec)
        assert (
            proc.returncode == 0
        ), f"render harness failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        return yaml.safe_load(proc.output_contents)

    return _render


@pytest.fixture
def run_validate() -> Callable[[dict[str, Any]], subprocess.CompletedProcess]:
    """Return a function that runs the validation harness; does NOT assert success."""

    def _validate(host_spec: dict[str, Any]) -> subprocess.CompletedProcess:
        return _run_harness(VALIDATE_HARNESS, host_spec)

    return _validate
