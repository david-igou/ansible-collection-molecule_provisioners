"""Build, install and exercise the real Molecule consumer interface."""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from .conftest import GuestRun, ROOT


def test_installed_molecule_consumer(aws_environment):
    import pexpect

    target = ROOT / ".build/aws-consumer" / uuid.uuid4().hex
    target.mkdir(parents=True, mode=0o700)
    artifacts = target / "artifacts"
    artifacts.mkdir()
    installed = target / "installed"
    subprocess.run(
        ["ansible-galaxy", "collection", "build", str(ROOT), "--output-path", str(artifacts)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ansible-galaxy",
            "collection",
            "install",
            str(next(artifacts.glob("*.tar.gz"))),
            "--no-deps",
            "--collections-path",
            str(installed),
        ],
        check=True,
        capture_output=True,
    )
    consumer = target / "consumer"
    shutil.copytree(ROOT / "tests/fixtures/aws-consumer", consumer)
    state = target / "state"
    state.mkdir(mode=0o700)
    env = dict(
        os.environ,
        MOLECULE_GLOB="extensions/molecule/*/molecule.yml",
        MOLECULE_EPHEMERAL_DIRECTORY=str(state),
        MP_TEST_AWS_IMAGE=aws_environment["image"],
        MP_TEST_AWS_CIDR=aws_environment["cidr"],
        ANSIBLE_COLLECTIONS_PATH=str(installed) + ":" + os.environ["ANSIBLE_COLLECTIONS_PATH"],
        PY_COLORS="0",
        ANSIBLE_FORCE_COLOR="0",
    )

    def molecule(action, *args):
        result = subprocess.run(
            ["molecule", action, *args],
            cwd=consumer,
            env=env,
            text=True,
            capture_output=True,
            timeout=1200,
        )
        log = target / (action + ".log")
        log.write_text(result.stdout + result.stderr)
        log.chmod(0o600)
        assert result.returncode == 0, "\n".join((result.stdout + result.stderr).splitlines()[-45:])
        return result

    run_id = None
    try:
        molecule("create")
        import yaml

        run_id = yaml.safe_load((state / "aws_run.yml").read_text())["id"]
        assert "instance" in molecule("list", "--format", "yaml").stdout
        child = pexpect.spawn(
            "molecule",
            ["login", "--host", "instance"],
            cwd=str(consumer),
            env=env,
            encoding="utf-8",
            timeout=90,
        )
        try:
            child.expect(r"[$#] ")
            child.sendline("printf '\\nAWS_MOLECULE_LOGIN_OK\\n'")
            child.expect(r"\r\nAWS_MOLECULE_LOGIN_OK\r\n")
            child.sendline("exit")
            child.expect(pexpect.EOF)
            child.close()
            assert child.exitstatus == 0
        finally:
            if child.isalive():
                child.close(force=True)
        for action in ("prepare", "converge", "idempotence", "verify"):
            molecule(action)
    finally:
        molecule("destroy")
        if run_id:
            checker = object.__new__(GuestRun)
            checker.environment, checker.run_id = aws_environment, run_id
            checker.assert_clean()
