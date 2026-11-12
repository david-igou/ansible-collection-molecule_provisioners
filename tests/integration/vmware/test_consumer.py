"""Opt-in installed consumer lifecycle against caller-selected VMware fixtures."""

import hashlib
import ipaddress
import os
import shutil
import ssl
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
REQUIRED = (
    "VMWARE_HOST",
    "VMWARE_USER",
    "VMWARE_PASSWORD",
    "MP_TEST_VMWARE_EXPECTED_UUID",
    "MP_TEST_VMWARE_DATACENTER",
    "MP_TEST_VMWARE_CLUSTER",
    "MP_TEST_VMWARE_FOLDER",
    "MP_TEST_VMWARE_DATASTORE",
    "MP_TEST_VMWARE_NETWORK",
    "MP_TEST_VMWARE_TEMPLATE",
    "MP_TEST_VMWARE_SECOND_TEMPLATE",
)
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_VMWARE_LIFECYCLE") != "1",
    reason="Explicit VMware lifecycle opt-in required",
)


def test_installed_consumer_lifecycle():
    """Authenticate real guests, preserve identity on retry and independently audit cleanup."""
    from pyVim.connect import SmartConnect, Disconnect
    from pyVmomi import vim
    import pexpect

    for name in REQUIRED:
        assert os.environ.get(name), "Set " + name
    si = SmartConnect(
        host=os.environ["VMWARE_HOST"],
        port=int(os.environ.get("VMWARE_PORT", "443")),
        user=os.environ["VMWARE_USER"],
        pwd=os.environ["VMWARE_PASSWORD"],
        sslContext=(
            ssl.create_default_context()
            if os.environ.get("VMWARE_VALIDATE_CERTS", "true").lower() not in ("false", "0", "no")
            else ssl._create_unverified_context()
        ),
        httpConnectionTimeout=30,
    )
    content = si.RetrieveContent()
    assert (
        content.about.instanceUuid == os.environ["MP_TEST_VMWARE_EXPECTED_UUID"]
    ), "Unexpected test vCenter identity"

    def vms():
        view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        try:
            return list(view.view)
        finally:
            view.Destroy()

    before = {
        vm._moId: (
            vm.name,
            vm.config.template,
            hashlib.sha256((vm.config.annotation or "").encode()).hexdigest(),
        )
        for vm in vms()
    }
    target = ROOT / ".build/vmware-live" / uuid.uuid4().hex
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
            "--force",
            "-p",
            str(installed),
        ],
        check=True,
        capture_output=True,
    )
    consumer = target / "consumer"
    shutil.copytree(ROOT / "tests/fixtures/vmware-consumer", consumer)
    # Let the installed collection lead the EE's normal search path.
    (consumer / "ansible.cfg").write_text("[defaults]\nhost_key_checking=False\n")
    state = target / "state"
    state.mkdir(mode=0o700)
    environment = dict(
        os.environ,
        MOLECULE_GLOB="extensions/molecule/*/molecule.yml",
        MOLECULE_EPHEMERAL_DIRECTORY=str(state),
        ANSIBLE_COLLECTIONS_PATH=str(installed) + ":" + os.environ["ANSIBLE_COLLECTIONS_PATH"],
        PY_COLORS="0",
        ANSIBLE_FORCE_COLOR="0",
    )

    def molecule(action, *args):
        result = subprocess.run(
            ["molecule", action, *args],
            cwd=consumer,
            env=environment,
            text=True,
            capture_output=True,
            timeout=1500,
            check=False,
        )
        log = target / (action + ".log")
        log.write_text(result.stdout + result.stderr)
        log.chmod(0o600)
        assert result.returncode == 0, "Molecule %s failed; private evidence: %s" % (action, log)
        return result

    def public_create(check_mode=False):
        scenario = consumer / "extensions/molecule/default"
        args = [
            "ansible-playbook",
            str(scenario / "create.yml"),
            "-i",
            str(scenario / "inventory"),
            "-i",
            str(state / "inventory"),
            "-e",
            "molecule_ephemeral_directory=" + str(state),
        ]
        if check_mode:
            args.append("--check")
        result = subprocess.run(
            args,
            cwd=consumer,
            env=environment,
            text=True,
            capture_output=True,
            timeout=1500,
            check=False,
        )
        log = target / ("check.log" if check_mode else "retry.log")
        log.write_text(result.stdout + result.stderr)
        log.chmod(0o600)
        assert result.returncode == 0, "Public create failed; private evidence: " + str(log)

    routes = []
    created_ids = []
    try:
        public_create(check_mode=True)
        assert not (state / "vmware_run.yml").exists()
        assert not (state / "identity_file").exists()
        assert {vm._moId for vm in vms()} == set(before)
        molecule("create")
        saved = yaml.safe_load((state / "vmware_run.yml").read_text())
        created_ids = [record["instance_uuid"] for record in saved["hosts"].values()]
        runtime = yaml.safe_load((state / "inventory/molecule_runtime.yml").read_text())["all"][
            "hosts"
        ]
        assert set(runtime) == {"ubuntu", "centos"}
        assert len(set(created_ids)) == 2
        assert (state / ".vmware-async").is_dir()
        assert all(path.stat().st_mode & 0o077 == 0 for path in (state / ".vmware-async").iterdir())
        assert "ubuntu" in molecule("list", "--format", "yaml").stdout
        route_host = os.environ.get("MP_TEST_VMWARE_ROUTE_HOST")
        if route_host:
            for entry in runtime.values():
                address = str(ipaddress.IPv4Address(entry["ansible_host"]))
                assert ipaddress.ip_address(address).is_private
                # A temporary /32 avoids the lab VPN/Docker overlap. Never replace an existing route.
                check = subprocess.run(
                    ["ssh", "-o", "BatchMode=yes", route_host, "ip route show " + address + "/32"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                if not check.stdout.strip():
                    subprocess.run(
                        [
                            "ssh",
                            "-o",
                            "BatchMode=yes",
                            route_host,
                            "sudo -n ip route add " + address + "/32 dev igou",
                        ],
                        check=True,
                        capture_output=True,
                    )
                    routes.append(address)
        molecule("prepare")
        for action in ("converge", "idempotence", "verify"):
            molecule(action)
        child = pexpect.spawn(
            "molecule",
            ["login", "--host", "ubuntu"],
            cwd=str(consumer),
            env=environment,
            encoding="utf-8",
            timeout=90,
        )
        try:
            child.expect(r"[$#] ")
            child.sendline("printf '\\nVMWARE_LOGIN_OK\\n'")
            child.expect(r"\r\nVMWARE_LOGIN_OK\r\n")
            child.sendline("exit")
            child.expect(pexpect.EOF)
        finally:
            if child.isalive():
                child.close(force=True)
        key_digest = hashlib.sha256((state / "identity_file").read_bytes()).digest()
        (state / "inventory/molecule_runtime.yml").unlink()
        public_create()
        retry = yaml.safe_load((state / "vmware_run.yml").read_text())
        assert [r["instance_uuid"] for r in retry["hosts"].values()] == created_ids
        assert hashlib.sha256((state / "identity_file").read_bytes()).digest() == key_digest
        assert (state / "inventory/molecule_runtime.yml").stat().st_mode & 0o777 == 0o600
    finally:
        try:
            molecule("destroy")
            molecule("destroy")
            assert not (state / "vmware_run.yml").exists()
            assert not (state / "identity_file").exists()
            assert not (state / ".vmware-async").exists()
            assert not (state / "inventory/molecule_runtime.yml").exists()
            remaining = vms()
            assert not set(created_ids).intersection(vm.config.instanceUuid for vm in remaining)
            assert {
                vm._moId: (
                    vm.name,
                    vm.config.template,
                    hashlib.sha256((vm.config.annotation or "").encode()).hexdigest(),
                )
                for vm in remaining
                if vm._moId in before
            } == before
        finally:
            for address in routes:
                subprocess.run(
                    [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        os.environ["MP_TEST_VMWARE_ROUTE_HOST"],
                        "sudo -n ip route del " + address + "/32 dev igou",
                    ],
                    check=True,
                    capture_output=True,
                )
            Disconnect(si)
