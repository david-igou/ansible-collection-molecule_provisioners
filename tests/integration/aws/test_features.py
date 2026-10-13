"""Check guest storage, secondary NICs and allowed/denied application traffic."""

import socket

import pytest

from .conftest import GuestRun

SERVER = """import socket, threading

def serve(kind, port):
    sock = socket.socket(socket.AF_INET, kind)
    sock.bind(('0.0.0.0', port))
    if kind == socket.SOCK_STREAM:
        sock.listen()
        while True:
            client, _ = sock.accept()
            client.sendall(b'aws-tcp-ok\\n')
            client.close()
    else:
        while True:
            data, address = sock.recvfrom(1024)
            sock.sendto(b'aws-udp-' + data, address)

for kind, port in ((socket.SOCK_STREAM, 18080), (socket.SOCK_DGRAM, 18081), (socket.SOCK_STREAM, 18082)):
    threading.Thread(target=serve, args=(kind, port), daemon=True).start()
threading.Event().wait()
"""


def test_shared_network_disks_reboot_secondary_nic_and_application_access(aws_environment):
    ports = [
        {"name": "tcp", "port": 18080, "cidrs": [aws_environment["cidr"], "10.77.0.0/24"]},
        {"name": "udp", "port": 18081, "protocol": "UDP", "cidrs": [aws_environment["cidr"]]},
    ]
    run = GuestRun(
        aws_environment,
        {
            "instance": {
                "root_disk": {"size": 12},
                "volumes": [{"device_name": "/dev/sdf", "ebs": {"volume_size": 1}}],
                "network_interfaces": [
                    {"device_index": 0},
                    {
                        "device_index": 1,
                        "private_ip_addresses": [
                            {"private_ip_address": "10.77.0.20", "primary": True}
                        ],
                    },
                ],
                "application_ports": ports,
            },
            "peer": {},
        },
    )
    try:
        run.action("create")
        state = run.state()
        run.run_id = state["id"]
        assert len(state["networks"]) == 1
        assert (
            state["hosts"]["instance"]["subnet"]["subnet_id"]
            == state["hosts"]["peer"]["subnet"]["subnet_id"]
        )
        ec2 = aws_environment["ec2"]
        instance = ec2.describe_instances(InstanceIds=[state["hosts"]["instance"]["instance_id"]])[
            "Reservations"
        ][0]["Instances"][0]
        assert len(instance["NetworkInterfaces"]) == 2
        secondary = next(
            nic["PrivateIpAddress"]
            for nic in instance["NetworkInterfaces"]
            if nic["Attachment"]["DeviceIndex"] == 1
        )
        assert secondary == "10.77.0.20"
        root_id = next(
            disk["Ebs"]["VolumeId"]
            for disk in instance["BlockDeviceMappings"]
            if disk["DeviceName"] == instance["RootDeviceName"]
        )
        root = ec2.describe_volumes(VolumeIds=[root_id])["Volumes"][0]
        assert root["Size"] == 12 and root["Encrypted"] and root["VolumeType"] == "gp3"
        volume = next(
            disk["Ebs"]["VolumeId"]
            for disk in instance["BlockDeviceMappings"]
            if disk["DeviceName"] == "/dev/sdf"
        )
        run.action("prepare")
        setup_disk = """import glob, pathlib, subprocess
volume = "VOLUME".replace('-', '')
path = next(path for path in glob.glob('/dev/disk/by-id/*') if volume in path and not path.endswith('-part1'))
if subprocess.run(['blkid', path], capture_output=True).returncode:
    subprocess.run(['mkfs.ext4', '-F', path], check=True, capture_output=True)
pathlib.Path('/mnt/test').mkdir(exist_ok=True)
subprocess.run(['mount', path, '/mnt/test'], check=True)
uuid = subprocess.check_output(['blkid', '-s', 'UUID', '-o', 'value', path], text=True).strip()
with open('/etc/fstab', 'a') as stream:
    stream.write('UUID=' + uuid + ' /mnt/test ext4 defaults,nofail 0 2\\n')
pathlib.Path('/mnt/test/persist').write_text('aws-data-ok')
pathlib.Path('/var/tmp/aws-root').write_text('aws-root-ok')
""".replace("VOLUME", volume)
        run.play(
            [
                {
                    "name": "Exercise actual guest storage and boot",
                    "hosts": "instance",
                    "gather_facts": False,
                    "become": True,
                    "tasks": [
                        {
                            "name": "Write root and data disk markers",
                            "ansible.builtin.command": {"argv": ["python3", "-c", setup_disk]},
                            "changed_when": True,
                        },
                        {
                            "name": "Reboot the provisioned guest",
                            "ansible.builtin.reboot": {"reboot_timeout": 240},
                        },
                        {
                            "name": "Read persistent disk markers",
                            "ansible.builtin.command": {
                                "argv": [
                                    "python3",
                                    "-c",
                                    (
                                        "from pathlib import Path; "
                                        "assert Path('/mnt/test/persist').read_text() == 'aws-data-ok'; "
                                        "assert Path('/var/tmp/aws-root').read_text() == 'aws-root-ok'"
                                    ),
                                ]
                            },
                            "changed_when": False,
                        },
                        {
                            "name": "Install application probes",
                            "ansible.builtin.copy": {
                                "dest": "/tmp/aws-ports.py",
                                "content": SERVER,
                                "mode": "0600",
                            },
                        },
                        {
                            "name": "Run application probes independently of the connection",
                            "ansible.builtin.command": {"argv": ["python3", "/tmp/aws-ports.py"]},
                            "async": 600,
                            "poll": 0,
                            "changed_when": True,
                        },
                        {
                            "name": "Wait for application probes locally",
                            "ansible.builtin.wait_for": {"port": 18080},
                        },
                    ],
                }
            ]
        )
        address = run.runtime()["instance"]["ansible_host"]
        with socket.create_connection((address, 18080), timeout=10) as sock:
            assert sock.recv(1024) == b"aws-tcp-ok\n"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(10)
            sock.sendto(b"ok", (address, 18081))
            assert sock.recv(1024) == b"aws-udp-ok"
        with pytest.raises((TimeoutError, OSError)):
            socket.create_connection((address, 18082), timeout=3)
        assert run.runtime()["instance"]["mp_aws_endpoints"]["udp"]["protocol"] == "UDP"
        run.play(
            [
                {
                    "name": "Verify secondary NIC traffic from another guest",
                    "hosts": "peer",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "Connect to application on secondary ENI",
                            "ansible.builtin.command": {
                                "argv": [
                                    "python3",
                                    "-c",
                                    "import socket; s=socket.create_connection(('"
                                    + secondary
                                    + "',18080),10); assert s.recv(1024) == b'aws-tcp-ok\\n'",
                                ]
                            },
                            "changed_when": False,
                        },
                    ],
                }
            ]
        )
    finally:
        run.destroy()


def test_arm_guest_and_architecture_mismatch(aws_environment):
    images = aws_environment["ec2"].describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["al2023-ami-2023.*-kernel-6.1-arm64"]},
            {"Name": "state", "Values": ["available"]},
        ],
    )["Images"]
    image = max(images, key=lambda image: (image["CreationDate"], image["ImageId"]))["ImageId"]
    run = GuestRun(aws_environment, {"instance": {"image_id": image, "instance_type": "t4g.micro"}})
    try:
        run.sources["all"]["children"]["molecule"]["hosts"]["instance"]["mp"]["aws"][
            "instance_type"
        ] = "t3.micro"
        from .conftest import write_yaml

        write_yaml(run.inventory, run.sources)
        code, _output = run.action("create", check=False)
        assert code != 0
        assert not (run.ephemeral / "aws_run.yml").exists()
        run.sources["all"]["children"]["molecule"]["hosts"]["instance"]["mp"]["aws"][
            "instance_type"
        ] = "t4g.micro"
        write_yaml(run.inventory, run.sources)
        run.action("create")
        run.action("prepare")
        run.play(
            [
                {
                    "name": "Verify a real ARM guest",
                    "hosts": "molecule",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "Read CPU architecture",
                            "ansible.builtin.command": "uname -m",
                            "register": "architecture",
                            "changed_when": False,
                        },
                        {
                            "name": "Require ARM64",
                            "ansible.builtin.assert": {"that": "architecture.stdout == 'aarch64'"},
                        },
                    ],
                }
            ]
        )
    finally:
        run.destroy()
