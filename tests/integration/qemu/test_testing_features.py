"""Local role regressions with real guests and authenticated connections."""

import hashlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_QEMU_TESTING") != "1",
    reason="Set RUN_QEMU_TESTING=1 for local QEMU guests.",
)
ROOT = Path(__file__).resolve().parents[3]
IMAGE = os.environ.get(
    "MP_TEST_QEMU_IMAGE",
    "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2",
)
CACHE = os.environ.get("MP_TEST_QEMU_CACHE", "/tmp/mp-qemu-functional-cache")


class GuestRun:
    """Exercise the public lifecycle inside the test EE."""

    def __init__(self, directory, hosts):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.inventory = directory / "hosts.yml"
        self.inventory.write_text(
            yaml.safe_dump({"all": {"children": {"molecule": {"hosts": hosts}}}})
        )
        self.extra = {
            "molecule_ephemeral_directory": str(directory),
            "mp_qemu_image_cache_dir": CACHE,
            "mp_qemu_wait_timeout": 300,
            "mp_qemu_cleanup_timeout": 5,
            "mp_qemu_ovmf_code": "/usr/share/OVMF/OVMF_CODE_4M.fd",
            "mp_qemu_ovmf_vars": "/usr/share/OVMF/OVMF_VARS_4M.fd",
        }

    def run(self, operation, check=True, timeout=600):
        playbook = self.directory / (operation + ".yml")
        if operation == "prepare":
            plays = [
                {
                    "name": "Authenticate guests",
                    "hosts": "molecule",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "Prepare",
                            "ansible.builtin.include_role": {
                                "name": "david_igou.molecule_provisioners.qemu",
                                "tasks_from": operation,
                            },
                        }
                    ],
                }
            ]
        else:
            plays = [
                {
                    "name": "Guest lifecycle",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "Provision",
                            "ansible.builtin.include_role": {
                                "name": "david_igou.molecule_provisioners.qemu",
                                "tasks_from": operation,
                            },
                        }
                    ],
                }
            ]
        playbook.write_text(yaml.safe_dump(plays))
        runtime_inventory = self.directory / "inventory"
        runtime_inventory.mkdir(exist_ok=True)
        proc = subprocess.run(
            [
                "ansible-playbook",
                "-i",
                str(self.inventory),
                "-i",
                str(runtime_inventory),
                str(playbook),
                "-e",
                json.dumps(self.extra),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        (self.directory / (operation + ".log")).write_text(proc.stdout + proc.stderr)
        if check:
            assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc

    def connection(self, host="guest"):
        return yaml.safe_load((self.directory / "inventory/molecule_runtime.yml").read_text())[
            "all"
        ]["hosts"][host]

    def ssh(self, command, host="guest", check=True, timeout=90):
        connection = self.connection(host)
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ConnectTimeout=5",
                "-i",
                connection["ansible_ssh_private_key_file"],
                "-p",
                str(connection["ansible_port"]),
                connection["ansible_user"] + "@127.0.0.1",
                command,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result


def guest(**options):
    return {
        "mp": {
            "qemu": {
                "image": IMAGE,
                "ssh_user": "debian",
                "memory": 768,
                "cpus": 1,
                **options,
            }
        }
    }


def test_overlapping_runs_disks_bootstrap_and_endpoints(tmp_path):
    reference = tmp_path / "reference.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(reference), "4M"], check=True)
    original_checksum = hashlib.sha256(reference.read_bytes()).hexdigest()
    config = guest(
        ssh_port=2222,
        data_disks=[
            {"name": "data", "size": "64M", "bus": "scsi", "serial": "functional-data"},
            {"name": "sata", "size": "32M", "bus": "sata", "serial": "functional-sata"},
            {
                "name": "reference",
                "image": reference.as_uri(),
                "bus": "virtio",
                "serial": "functional-reference",
                "read_only": True,
            },
        ],
        cloud_init={
            "user_data": {
                "write_files": [
                    {"path": "/opt/bootstrap-marker", "content": "custom-bootstrap"},
                    {
                        "path": "/etc/ssh/sshd_config.d/molecule-port.conf",
                        "content": "Port 2222\n",
                    },
                ],
                "runcmd": ["systemctl restart ssh"],
            }
        },
        application_ports=[
            {"name": "http", "protocol": "TCP", "port": 8080},
            {"name": "udp", "protocol": "UDP", "port": 5353},
        ],
        connection_vars={"ansible_python_interpreter": "/usr/bin/python3"},
    )
    first = GuestRun(tmp_path / "a", {"guest": config})
    second = GuestRun(tmp_path / "b", {"guest": guest()})
    try:
        first.run("create")
        second.run("create")
        first.run("prepare")
        second.run("prepare")
        assert first.connection()["ansible_port"] != second.connection()["ansible_port"]
        assert "custom-bootstrap" in first.ssh("cat /opt/bootstrap-marker").stdout
        # Select by serial instead of assuming device enumeration order.
        first.ssh(
            'sudo sh -c \'dev=$(lsblk -ndo PATH,SERIAL | awk "\\$2 == \\"functional-data\\" {print \\$1}"); mkfs.ext4 -q $dev; mkdir -p /mnt/data; mount $dev /mnt/data; echo sentinel >/mnt/data/value; umount /mnt/data\''
        )
        first.ssh(
            'sudo sh -c \'dev=$(lsblk -ndo PATH,SERIAL | awk "\\$2 == \\"functional-data\\" {print \\$1}"); mount $dev /mnt/data; cat /mnt/data/value\''
        )
        assert "functional-sata" in first.ssh("lsblk -ndo SERIAL").stdout
        devices = json.loads(first.ssh("lsblk --json --nodeps -o PATH,SERIAL,RO").stdout)[
            "blockdevices"
        ]
        readonly = next(disk for disk in devices if disk.get("serial") == "functional-reference")
        assert readonly["ro"]
        assert (
            first.ssh(
                "sudo dd if=/dev/zero of=" + readonly["path"] + " bs=512 count=1 status=none",
                check=False,
            ).returncode
            != 0
        )
        state_before = json.loads((first.directory / "qemu-state.json").read_text())
        first.run("create")
        assert (
            json.loads((first.directory / "qemu-state.json").read_text())["hosts"]["guest"]["pid"]
            == state_before["hosts"]["guest"]["pid"]
        )
        assert "sentinel" in first.ssh("cat /mnt/data/value").stdout
        first.ssh(
            "nohup python3 -m http.server 8080 --directory /opt >/tmp/http.log 2>&1 </dev/null &"
        )
        udp_server = "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('0.0.0.0',5353)); data,peer=s.recvfrom(100); s.sendto(b'reply:'+data,peer)"
        first.ssh(
            "nohup python3 -c "
            + __import__("shlex").quote(udp_server)
            + " >/tmp/udp.log 2>&1 </dev/null &"
        )
        endpoints = first.connection()["mp_qemu_endpoints"]
        from urllib.request import urlopen

        for attempt in range(20):
            try:
                with urlopen(
                    "http://127.0.0.1:{}/bootstrap-marker".format(endpoints["http"]["port"]),
                    timeout=5,
                ) as response:
                    assert response.read() == b"custom-bootstrap"
                break
            except OSError:
                time.sleep(1)
        else:
            pytest.fail("HTTP forward never served the application.")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(5)
            for attempt in range(10):
                client.sendto(b"probe", ("127.0.0.1", endpoints["udp"]["port"]))
                try:
                    assert client.recv(100) == b"reply:probe"
                    break
                except TimeoutError:
                    continue
            else:
                pytest.fail("UDP application never answered through its forward.")
        # Reboot and remount confirms persistence across an actual guest reboot.
        first.ssh("sudo reboot", check=False)
        time.sleep(3)
        first.run("prepare")
        assert (
            "sentinel"
            in first.ssh(
                'sudo sh -c \'dev=$(lsblk -ndo PATH,SERIAL | awk "\\$2 == \\"functional-data\\" {print \\$1}"); mount $dev /mnt/data; cat /mnt/data/value\''
            ).stdout
        )
        first.run("destroy")
        assert second.ssh("echo surviving-run").stdout.strip() == "surviving-run"
        assert not (first.directory / "inventory/molecule_runtime.yml").exists()
    finally:
        first.run("destroy")
        second.run("destroy")
    assert hashlib.sha256(reference.read_bytes()).hexdigest() == original_checksum


def test_three_guest_routing_and_network_config(tmp_path):
    networks = [
        {"name": "lan-a", "type": "socket"},
        {"name": "lan-b", "type": "socket"},
    ]
    configs = {}
    for host, nics, addresses in [
        (
            "router",
            [("a", "lan-a", "52:54:00:10:00:01"), ("b", "lan-b", "52:54:00:20:00:01")],
            ["192.0.2.1/24", "198.51.100.1/24"],
        ),
        ("client-a", [("a", "lan-a", "52:54:00:10:00:02")], ["192.0.2.2/24"]),
        ("client-b", [("b", "lan-b", "52:54:00:20:00:02")], ["198.51.100.2/24"]),
    ]:
        ethernets = {
            name: {
                "match": {"macaddress": mac},
                "set-name": name,
                "addresses": [address],
            }
            for (name, network, mac), address in zip(nics, addresses)
        }
        configs[host] = guest(
            networks=networks,
            nics=[{"name": name, "network": network, "mac": mac} for name, network, mac in nics],
            cloud_init={"network_data": {"version": 2, "ethernets": ethernets}},
        )
    run = GuestRun(tmp_path / "net", configs)
    try:
        run.run("create")
        run.run("prepare")
        run.ssh("sudo sysctl -w net.ipv4.ip_forward=1", "router")
        run.ssh("sudo ip route add 198.51.100.0/24 via 192.0.2.1", "client-a")
        run.ssh("sudo ip route add 192.0.2.0/24 via 198.51.100.1", "client-b")
        run.ssh("ping -c 3 -W 3 198.51.100.2", "client-a")
        run.ssh("sudo iptables -I FORWARD -s 192.0.2.2 -d 198.51.100.2 -j DROP", "router")
        assert run.ssh("ping -c 1 -W 2 198.51.100.2", "client-a", check=False).returncode != 0
        run.ssh("sudo iptables -D FORWARD -s 192.0.2.2 -d 198.51.100.2 -j DROP", "router")
        run.ssh("ping -c 1 -W 3 198.51.100.2", "client-a")
    finally:
        run.run("destroy")


def test_failed_boot_diagnostics_and_partial_create(tmp_path):
    image = tmp_path / "empty.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(image), "1M"], check=True)
    run = GuestRun(
        tmp_path / "fail",
        {
            "guest": guest(
                image=image.as_uri(),
                cloud_init={"enabled": False},
                diagnostics={"retain_logs": True},
            )
        },
    )
    run.extra["mp_qemu_wait_timeout"] = 3
    try:
        run.run("create")
        result = run.run("prepare", check=False)
        assert result.returncode != 0
        assert "serial_log" in result.stdout and "stderr_log" in result.stdout
        state = json.loads((run.directory / "qemu-state.json").read_text())
        serial = Path(state["hosts"]["guest"]["serial_log"])
        assert serial.stat().st_mode & 0o777 == 0o600
        run.run("destroy")
        assert serial.exists()
        assert not (run.directory / "guest.qcow2").exists()
    finally:
        run.run("destroy")


def test_authenticated_network_cli_guest(tmp_path):
    """Exercise the real RouterOS connection plugin against a test CLI shell."""
    cli = """#!/usr/bin/python3
import sys
prompt = '[molecule@test-router] > '
print(prompt, end='', flush=True)
for line in sys.stdin:
    command = line.strip()
    if command == '/system identity print':
        print('  name: test-router', flush=True)
    print(prompt, end='', flush=True)
"""
    config = guest(
        connection="network_cli",
        connection_probe="/system identity print",
        ssh_user="molecule",
        cloud_init={
            "enabled": True,
            "inject_ssh_key": True,
            "user_data": {
                "users": [
                    {
                        "name": "molecule",
                        "shell": "/usr/local/bin/test-cli",
                        "sudo": False,
                    }
                ],
                "write_files": [
                    {
                        "path": "/usr/local/bin/test-cli",
                        "permissions": "0755",
                        "content": cli,
                    }
                ],
            },
        },
        connection_vars={
            "ansible_network_os": "community.routeros.routeros",
            "ansible_network_cli_ssh_type": "paramiko",
            "ansible_connect_timeout": 10,
            "ansible_command_timeout": 10,
        },
    )
    run = GuestRun(tmp_path / "cli", {"guest": config})
    try:
        run.run("create")
        time.sleep(15)
        run.run("prepare")
        # Authentication and a platform-specific CLI command, not just TCP reachability.
        assert run.connection()["ansible_connection"] == "ansible.netcommon.network_cli"
    finally:
        run.run("destroy")


def test_uefi_variable_survives_repeated_create_and_reboot(tmp_path):
    import shlex

    run = GuestRun(tmp_path / "efi", {"guest": guest(firmware="uefi")})
    variable = "/sys/firmware/efi/efivars/MoleculeTest-2ba40c04-245c-4662-ad0b-53d40bd17f29"
    try:
        run.run("create")
        run.run("prepare")
        script = f"from pathlib import Path; Path({variable!r}).write_bytes(bytes([7,0,0,0])+b'molecule-variable')"
        run.ssh("sudo python3 -c " + shlex.quote(script))
        state = json.loads((run.directory / "qemu-state.json").read_text())
        before = hashlib.sha256(Path(state["hosts"]["guest"]["ovmf_vars"]).read_bytes()).hexdigest()
        run.run("create")
        assert (
            hashlib.sha256(Path(state["hosts"]["guest"]["ovmf_vars"]).read_bytes()).hexdigest()
            == before
        )
        run.ssh("sudo reboot", check=False)
        time.sleep(3)
        run.run("prepare")
        check = f"from pathlib import Path; print(Path({variable!r}).read_bytes()[4:].decode())"
        assert "molecule-variable" in run.ssh("sudo python3 -c " + shlex.quote(check)).stdout
    finally:
        run.run("destroy")


def test_partial_create_cleanup_uses_manifest_after_inventory_changes(tmp_path):
    image = tmp_path / "blank.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(image), "1M"], check=True)
    run = GuestRun(
        tmp_path / "partial",
        {
            "first": guest(image=image.as_uri(), cloud_init={"enabled": False}, extra_args=["-S"]),
            "second": guest(
                image=image.as_uri(),
                cloud_init={"enabled": False},
                extra_args=["-invalid-test-option"],
            ),
        },
    )
    try:
        result = run.run("create", check=False)
        assert result.returncode != 0
        state = json.loads((run.directory / "qemu-state.json").read_text())
        assert state["hosts"]["first"]["status"] == "running"
        assert state["hosts"]["second"]["status"] == "launching"
        inventory = yaml.safe_load(run.inventory.read_text())
        del inventory["all"]["children"]["molecule"]["hosts"]["second"]
        run.inventory.write_text(yaml.safe_dump(inventory))
        run.run("destroy")
        assert not (run.directory / "first.qcow2").exists()
        assert not (run.directory / "second.qcow2").exists()
        assert not (run.directory / "qemu-state.json").exists()
    finally:
        run.run("destroy")
