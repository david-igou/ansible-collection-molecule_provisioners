"""Fast, VM-less tests for the qemu role's validation and merge logic."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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


def test_bad_driver_fails_with_message() -> None:
    proc = _run("run_validate.yml", "bad_driver.yml")
    assert proc.returncode != 0
    assert "unsupported qemu.driver 'vmware'" in proc.stdout


def test_process_nat_combo_fails_with_message() -> None:
    proc = _run("run_validate.yml", "process_nat_invalid.yml")
    assert proc.returncode != 0
    assert "driver=process with network.mode=nat" in proc.stdout


def test_missing_image_fails_with_message() -> None:
    proc = _run("run_validate.yml", "missing_image.yml")
    assert proc.returncode != 0
    assert "is missing qemu.image" in proc.stdout
