"""Tests for ssh_service.type=PodIP mode (VMI pod-network address, no Service).

The role must:
 - Allow ssh_service.type=PodIP in the allow-list.
 - Skip the cluster-Node lookup for PodIP hosts (no ``nodes`` RBAC needed).
 - Resolve ansible_host to the VMI's status.interfaces[0].ipAddress.
 - Default ansible_port to the guest port (22 ssh / 5986 psrp-winrm); honor
   ssh_service.port override.
"""

from __future__ import annotations

import json
import subprocess
import tempfile

from pathlib import Path


HERE = Path(__file__).parent
PORT_HARNESS = HERE / "ssh_port_render_harness.yml"
GATING_HARNESS = HERE / "connection_ip_gating_harness.yml"
INVENTORY_HARNESS = HERE / "connection_inventory_harness.yml"


def _run(harness: Path, extra_vars: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "out.json"
        extra_vars = dict(extra_vars, output_path=str(output_path))
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
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(output_path.read_text())


def test_podip_mode_defaults_port_to_22() -> None:
    result = _run(PORT_HARNESS, {"spec": {"ssh_service": {"type": "PodIP"}}})
    assert result["ansible_port"] == 22


def test_podip_mode_defaults_port_to_5986_for_winrm() -> None:
    result = _run(
        PORT_HARNESS,
        {"spec": {"ssh_service": {"type": "PodIP"}, "connection": "psrp"}},
    )
    assert result["ansible_port"] == 5986


def test_podip_mode_honors_port_override() -> None:
    result = _run(
        PORT_HARNESS,
        {"spec": {"ssh_service": {"type": "PodIP", "port": 2222}}},
    )
    assert result["ansible_port"] == 2222


def test_podip_hosts_skip_node_lookup() -> None:
    result = _run(
        GATING_HARNESS,
        {"specs": {"instance": {"ssh_service": {"type": "PodIP"}}}},
    )
    assert result["needs_node_lookup"] is False


def test_nodeport_hosts_still_need_node_lookup() -> None:
    result = _run(
        GATING_HARNESS,
        {
            "specs": {
                "a": {"ssh_service": {"type": "PodIP"}},
                "b": {"ssh_service": {"type": "NodePort"}},
            }
        },
    )
    assert result["needs_node_lookup"] is True


def test_podip_inventory_uses_vmi_ip() -> None:
    result = _run(
        INVENTORY_HARNESS,
        {"spec": {"ssh_service": {"type": "PodIP"}, "ssh_user": "ubuntu"}},
    )
    assert result["ansible_host"] == "10.130.0.42"
    assert result["ansible_port"] == 22
