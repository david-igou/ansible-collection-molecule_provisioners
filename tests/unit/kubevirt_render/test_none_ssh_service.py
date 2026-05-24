"""Tests for ssh_service.type=None mode (skip Service creation).

Regression for #31. The role must:
 - Allow ssh_service.type=None in the allow-list.
 - Reject None mode without connection_ip (validate).
 - Skip the NodePort Service lookup in the runtime-inventory builder.
 - Default ansible_port to 22; honor ssh_service.port override.
"""

from __future__ import annotations

import json
import subprocess
import tempfile

from pathlib import Path


HERE = Path(__file__).parent
PORT_HARNESS = HERE / "ssh_port_render_harness.yml"


def _run_port(spec: dict) -> dict:
    """Render the _ansible_port expression for a single host spec."""
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "out.json"
        extra_vars = {"spec": spec, "output_path": str(output_path)}
        proc = subprocess.run(
            [
                "ansible-playbook",
                str(PORT_HARNESS),
                "--extra-vars",
                json.dumps(extra_vars),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(output_path.read_text())


def test_none_mode_defaults_port_to_22() -> None:
    result = _run_port(
        {
            "ssh_service": {"type": "None"},
            "connection_ip": "10.0.0.1",
        }
    )
    assert result["ansible_port"] == 22


def test_none_mode_honors_port_override() -> None:
    result = _run_port(
        {
            "ssh_service": {"type": "None", "port": 2222},
            "connection_ip": "10.0.0.1",
        }
    )
    assert result["ansible_port"] == 2222


def test_nodeport_mode_uses_service_nodeport() -> None:
    """NodePort mode must still resolve from the Service's nodePort."""
    result = _run_port(
        {
            "ssh_service": {"type": "NodePort"},
        }
    )
    assert result["ansible_port"] == 31234  # synthetic nodePort in the harness


def test_default_mode_uses_service_nodeport() -> None:
    """Unset ssh_service defaults to NodePort mode."""
    result = _run_port({})
    assert result["ansible_port"] == 31234


def test_none_mode_without_connection_ip_fails_validate(run_validate) -> None:
    proc = run_validate(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/foo/bar:latest"},
            "ssh_service": {"type": "None"},
        }
    )
    assert proc.returncode != 0
    assert "connection_ip" in proc.stdout


def test_none_mode_with_connection_ip_passes_validate(run_validate) -> None:
    proc = run_validate(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/foo/bar:latest"},
            "ssh_service": {"type": "None"},
            "connection_ip": "10.0.0.1",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
