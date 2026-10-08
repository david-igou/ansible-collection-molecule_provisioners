"""QEMU validation, cache, and paused-guest launch regression tests."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess

from pathlib import Path

import pytest
import yaml


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
    assert "is missing qemu.image" in proc.stdout


def test_image_cache_creates_cached_file() -> None:
    proc = _run("run_image_cache.yml", "valid_local_image.yml")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_seed_iso_is_built_and_contains_user(tmp_path) -> None:
    import os

    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            str(FIXTURES / "valid_local_image.yml"),
            str(ASSERTIONS / "run_seed_iso.yml"),
        ],
        cwd=COLLECTION_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("firmware", ["bios", "uefi"])
@pytest.mark.parametrize(
    "kvm, cpu_model, expected",
    [
        (False, None, "Nehalem"),
        (False, "", "Nehalem"),
        (False, "Westmere", "Westmere"),
        (True, None, "host"),
        (True, "Nehalem", "Nehalem"),
    ],
    ids=["tcg-default", "tcg-empty", "tcg-override", "kvm-default", "kvm-override"],
)
def test_cpu_model_launch(tmp_path, firmware, kvm, cpu_model, expected) -> None:
    """Exercise CPU selection in the real BIOS/UEFI launch tasks (#37, #44)."""
    if not shutil.which("qemu-system-x86_64"):
        pytest.skip("qemu-system-x86_64 not installed")
    if kvm and not os.access("/dev/kvm", os.R_OK | os.W_OK):
        pytest.skip("KVM not available")

    extra_vars = {
        "molecule_ephemeral_directory": str(tmp_path),
        "mp_qemu_image_cache_dir": str(tmp_path / "cache"),
        "_mp_qemu_kvm_ok": kvm,
        "expected_cpu_model": expected,
    }
    if firmware == "uefi":
        for code, variables in [
            ("/usr/share/edk2/ovmf/OVMF_CODE.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd"),
            ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
            ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
        ]:
            if Path(code).is_file() and Path(variables).is_file():
                extra_vars.update(mp_qemu_ovmf_code=code, mp_qemu_ovmf_vars=variables)
                break
        else:
            pytest.skip("OVMF firmware not installed")

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        extra_vars["mp_qemu_slirp_port_base"] = listener.getsockname()[1]

    image = tmp_path / "base.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(image), "1M"], check=True)
    spec = {
        "image": image.as_uri(),
        "memory": 64,
        "cpus": 1,
        "firmware": firmware,
        "extra_args": ["-S"],
    }
    if cpu_model is not None:
        spec["cpu_model"] = cpu_model
    inventory = tmp_path / "hosts.yml"
    inventory.write_text(
        yaml.safe_dump(
            {"all": {"children": {"molecule": {"hosts": {"cpu-test": {"mp": {"qemu": spec}}}}}}}
        ),
    )
    proc = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            str(inventory),
            str(ASSERTIONS / "run_cpu_model_launch.yml"),
            "-e",
            json.dumps(extra_vars),
        ],
        cwd=COLLECTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_destroy_is_idempotent_on_fresh_state(tmp_path) -> None:
    import os

    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            str(FIXTURES / "valid_local_image.yml"),
            str(ASSERTIONS / "run_destroy.yml"),
        ],
        cwd=COLLECTION_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.slow
def test_process_driver_e2e(tmp_path) -> None:
    import os
    import shutil

    if not shutil.which("qemu-system-x86_64"):
        pytest.skip("qemu-system-x86_64 not installed")
    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            str(FIXTURES / "process.yml"),
            str(ASSERTIONS / "run_process_e2e.yml"),
        ],
        cwd=COLLECTION_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
