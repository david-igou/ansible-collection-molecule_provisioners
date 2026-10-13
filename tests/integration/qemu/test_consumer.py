"""Verify an installed collection from a separate Molecule consumer."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_QEMU_TESTING") != "1",
    reason="Set RUN_QEMU_TESTING=1 for local QEMU guests.",
)
ROOT = Path(__file__).resolve().parents[3]


def test_installed_consumer_list_login_and_lifecycle(tmp_path):
    """Authenticate through Molecule login after an out-of-process create."""
    import pexpect

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    installed = tmp_path / "installed"
    subprocess.run(
        [
            "ansible-galaxy",
            "collection",
            "build",
            str(ROOT),
            "--output-path",
            str(artifacts),
        ],
        check=True,
        capture_output=True,
    )
    archive = next(artifacts.glob("*.tar.gz"))
    subprocess.run(
        [
            "ansible-galaxy",
            "collection",
            "install",
            str(archive),
            "--no-deps",
            "--collections-path",
            str(installed),
        ],
        check=True,
        capture_output=True,
    )
    consumer = tmp_path / "consumer"
    shutil.copytree(ROOT / "tests/fixtures/qemu-consumer", consumer)
    env = os.environ.copy()
    env["ANSIBLE_COLLECTIONS_PATH"] = (
        str(installed)
        + ":"
        + env.get("ANSIBLE_COLLECTIONS_PATH", str(Path.home() / ".ansible/collections"))
    )
    env["PY_COLORS"] = "0"
    env["ANSIBLE_FORCE_COLOR"] = "0"

    def molecule(action, *arguments):
        result = subprocess.run(
            ["molecule", action, *arguments],
            cwd=consumer,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    try:
        molecule("create")
        listing = molecule("list", "--format", "yaml")
        assert "instance" in listing.stdout
        child = pexpect.spawn(
            "molecule",
            ["login", "--host", "instance"],
            cwd=str(consumer),
            env=env,
            encoding="utf-8",
            timeout=60,
        )
        try:
            child.expect(r"[$#] ")
            child.sendline("printf '\\nMOLECULE_LOGIN_OK\\n'")
            child.expect(r"\r\nMOLECULE_LOGIN_OK\r\n")
            child.expect(r"[$#] ")
            child.sendline("exit")
            child.expect(pexpect.EOF)
            child.close()
            assert child.exitstatus == 0
        finally:
            if child.isalive():
                child.close(force=True)
        molecule("converge")
        molecule("verify")
    finally:
        molecule("destroy")
