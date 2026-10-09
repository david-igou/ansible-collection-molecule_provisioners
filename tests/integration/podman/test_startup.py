"""Opt-in lifecycle checks for guests that exit during Podman creation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PODMAN_STARTUP") != "1", reason="requires a working Podman runtime"
)
ROOT = Path(__file__).resolve().parents[3]
IMAGE = os.environ.get("MP_PODMAN_TEST_IMAGE", "docker.io/library/nginx:stable-alpine")


class Run:
    def __init__(self, path, commands):
        self.path = path
        self.names = [f"mp-startup-{uuid4().hex[:12]}" for _command in commands]
        self.runtime = path / "inventory" / "molecule_runtime.yml"
        self.runtime.parent.mkdir()
        hosts = {
            name: {
                "mp": {
                    "podman": {
                        "image": IMAGE,
                        "command": command,
                        "privileged": False,
                        "env": {"MP_TEST_PRIVATE_VALUE": "dummy-private-value"},
                    }
                }
            }
            for name, command in zip(self.names, commands)
        }
        inventory = {
            "all": {
                "vars": {"mp_backend": "podman"},
                "children": {"molecule": {"hosts": hosts}},
            }
        }
        self.inventory = path / "hosts.yml"
        self.inventory.write_text(yaml.safe_dump(inventory, sort_keys=False))
        self.variables = path / "variables.yml"
        self.variables.write_text(
            yaml.safe_dump(
                {
                    "molecule_ephemeral_directory": str(path),
                    "mp_podman_async_delay": 1,
                    "mp_podman_async_retries": 60,
                }
            )
        )

    def play(self, action):
        return subprocess.run(
            [
                "ansible-playbook",
                "-i",
                str(self.inventory),
                "-e",
                f"@{self.variables}",
                str(ROOT / "playbooks" / f"{action}.yml"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def state(self, name):
        proc = subprocess.run(
            ["podman", "inspect", "--format", "{{json .State}}", name],
            capture_output=True,
            text=True,
            check=False,
        )
        return json.loads(proc.stdout) if proc.returncode == 0 else None

    def cleanup(self):
        proc = self.play("destroy")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert all(self.state(name) is None for name in self.names)


def test_healthy_guest_remains_accessible_across_create_calls(tmp_path):
    run = Run(tmp_path, [["sleep", "infinity"]])
    try:
        for _attempt in range(2):
            proc = run.play("create")
            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert run.state(run.names[0])["Running"]
            proc = subprocess.run(
                ["podman", "exec", run.names[0], "echo", "guest-accessible"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert proc.returncode == 0 and "guest-accessible" in proc.stdout
            inventory = yaml.safe_load(run.runtime.read_text())
            assert inventory["all"]["hosts"][run.names[0]]["ansible_connection"] == (
                "containers.podman.podman"
            )
    finally:
        run.cleanup()
    run.cleanup()


def test_missing_guest_reports_an_actionable_failure(tmp_path):
    run = Run(tmp_path, [["sleep", "infinity"]])
    playbook = tmp_path / "check.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Check a missing guest",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {"__mp_podman_host": run.names[0]},
                    "tasks": [
                        {
                            "name": "Check container startup",
                            "ansible.builtin.include_role": {
                                "name": "david_igou.molecule_provisioners.podman",
                                "tasks_from": "_check_running",
                            },
                        }
                    ],
                }
            ],
            sort_keys=False,
        )
    )
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(run.inventory), str(playbook)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "status=missing" in proc.stdout
    assert "exit_code=unknown" in proc.stdout
    assert run.names[0] in proc.stdout
    assert "podman logs --tail 20" in proc.stdout


@pytest.mark.parametrize("exit_code,delay", [(0, 0), (37, 0), (37, 1)])
def test_exited_guest_fails_create_and_can_be_destroyed(tmp_path, exit_code, delay):
    run = Run(
        tmp_path,
        [["sleep", "infinity"], ["sh", "-c", f"sleep {delay}; exit {exit_code}"]],
    )
    try:
        proc = run.play("create")
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert run.names[1] in proc.stdout
        assert "status=exited" in proc.stdout
        assert f"exit_code={exit_code}" in proc.stdout
        assert "podman logs --tail 20" in proc.stdout
        assert "dummy-private-value" not in proc.stdout + proc.stderr
        assert not run.runtime.exists()
        assert run.state(run.names[0])["Running"]
        assert run.state(run.names[1])["ExitCode"] == exit_code
    finally:
        run.cleanup()
    run.cleanup()
