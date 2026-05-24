"""Render-only checks for the connection_ip opt-out gating in create.yml.

Regression for #30: the role must skip the cluster-scoped Node lookup when
every host pins its own connection_ip, so namespace-scoped service accounts
without `nodes [get,list]` RBAC can drive this role.
"""

from __future__ import annotations

import json
import subprocess
import tempfile

from pathlib import Path


HERE = Path(__file__).parent
HARNESS = HERE / "connection_ip_gating_harness.yml"


def _run(specs: dict[str, dict]) -> dict:
    """Run the gating harness with a synthetic _mp_specs dict, return parsed output."""
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "out.json"
        extra_vars = {"specs": specs, "output_path": str(output_path)}
        proc = subprocess.run(
            [
                "ansible-playbook",
                str(HARNESS),
                "--extra-vars",
                json.dumps(extra_vars),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(output_path.read_text())


def test_all_hosts_pinned_skips_lookup() -> None:
    """When every host sets connection_ip, the cluster-Node lookup must be skipped."""
    result = _run(
        {
            "host-a": {"connection_ip": "10.0.0.1"},
            "host-b": {"connection_ip": "10.0.0.2"},
        }
    )
    assert result["needs_node_lookup"] is False


def test_one_host_unpinned_triggers_lookup() -> None:
    """If any host omits connection_ip, the lookup is required."""
    result = _run(
        {
            "host-a": {"connection_ip": "10.0.0.1"},
            "host-b": {},  # no connection_ip — falls back to Node IP
        }
    )
    assert result["needs_node_lookup"] is True


def test_no_hosts_pinned_triggers_lookup() -> None:
    """The default case (no host pins connection_ip) preserves existing behavior."""
    result = _run({"host-a": {}, "host-b": {}})
    assert result["needs_node_lookup"] is True


def test_single_host_pinned_skips_lookup() -> None:
    """A single host with connection_ip set skips the lookup."""
    result = _run({"only-host": {"connection_ip": "192.0.2.10"}})
    assert result["needs_node_lookup"] is False
