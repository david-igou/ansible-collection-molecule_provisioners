"""Fast, daemon-less tests for the docker role's validation and merge logic."""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
ASSERTIONS = HERE / "assertions"
COLLECTION_ROOT = HERE.parent.parent.parent  # ansible_collections/.../molecule_provisioners


def _run(playbook: str, inventory: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / inventory), str(ASSERTIONS / playbook)],
        cwd=COLLECTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_minimal_passes_validation() -> None:
    proc = _run("run_validate.yml", "valid_minimal.yml")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_missing_image_fails_with_message() -> None:
    proc = _run("run_validate.yml", "missing_image.yml")
    assert proc.returncode != 0
    assert "is missing docker.image" in proc.stdout


def test_networks_collector_dedupes_and_skips_reserved() -> None:
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / "networks_and_reserved.yml"),
         str(ASSERTIONS / "run_networks.yml")],
        cwd=COLLECTION_ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_destroy_is_idempotent_on_fresh_state(tmp_path) -> None:
    import os
    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / "valid_minimal.yml"),
         str(ASSERTIONS / "run_destroy.yml")],
        cwd=COLLECTION_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
