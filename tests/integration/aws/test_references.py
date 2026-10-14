"""Preserve caller-owned snapshot storage and ENIs over a private SSH route."""

from .conftest import GuestRun


def test_snapshot_existing_volume_and_eni_are_preserved(aws_environment):
    ec2 = aws_environment["ec2"]
    foundation = GuestRun(
        aws_environment,
        {
            "instance": {
                "volumes": [{"device_name": "/dev/sdf", "ebs": {"volume_size": 1}}],
                "management_cidrs": [aws_environment["cidr"], "10.77.0.0/24"],
            }
        },
    )
    consumer = None
    snapshot_id = volume_id = eni_id = None
    try:
        foundation.action("create")
        foundation.action("prepare")
        host = foundation.state()["hosts"]["instance"]
        instance = ec2.describe_instances(InstanceIds=[host["instance_id"]])["Reservations"][0][
            "Instances"
        ][0]
        source = next(
            disk["Ebs"]["VolumeId"]
            for disk in instance["BlockDeviceMappings"]
            if disk["DeviceName"] == "/dev/sdf"
        )
        setup = """import glob, subprocess
path = next(path for path in glob.glob('/dev/disk/by-id/*') if 'VOLUME' in path)
subprocess.run(['mkfs.ext4', '-F', path], check=True, capture_output=True)
subprocess.run(['mkdir', '-p', '/mnt/source'], check=True)
subprocess.run(['mount', path, '/mnt/source'], check=True)
with open('/mnt/source/snapshot-marker', 'w') as stream:
    stream.write('snapshot-storage-ok')
subprocess.run(['sync'], check=True)
subprocess.run(['umount', '/mnt/source'], check=True)
""".replace("VOLUME", source.replace("-", ""))
        foundation.play(
            [
                {
                    "name": "Seed storage before taking a snapshot",
                    "hosts": "molecule",
                    "gather_facts": False,
                    "become": True,
                    "tasks": [
                        {
                            "name": "Write a snapshot marker to an actual EBS volume",
                            "ansible.builtin.command": {"argv": ["python3", "-c", setup]},
                            "changed_when": True,
                        },
                    ],
                }
            ]
        )
        snapshot_id = ec2.create_snapshot(
            VolumeId=source, Description="Disposable provisioner snapshot test"
        )["SnapshotId"]
        ec2.get_waiter("snapshot_completed").wait(
            SnapshotIds=[snapshot_id], WaiterConfig={"Delay": 5, "MaxAttempts": 120}
        )
        caller_tags = [{"Key": "provisioner-reference-test", "Value": foundation.state()["id"]}]
        volume_id = ec2.create_volume(
            SnapshotId=snapshot_id,
            AvailabilityZone=host["subnet"]["availability_zone"],
            VolumeType="gp3",
            Encrypted=True,
            TagSpecifications=[{"ResourceType": "volume", "Tags": caller_tags}],
        )["VolumeId"]
        ec2.get_waiter("volume_available").wait(
            VolumeIds=[volume_id], WaiterConfig={"Delay": 3, "MaxAttempts": 60}
        )
        interface = ec2.create_network_interface(
            SubnetId=host["subnet"]["subnet_id"],
            Groups=host["groups"],
            TagSpecifications=[{"ResourceType": "network-interface", "Tags": caller_tags}],
        )["NetworkInterface"]
        eni_id = interface["NetworkInterfaceId"]
        jump = foundation.runtime()["instance"]["ansible_host"]
        key = str(foundation.ephemeral / "identity_file")
        proxy = (
            '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ProxyCommand="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i '
            + key
            + " -W %h:%p cloud-user@"
            + jump
            + '"'
        )
        consumer = GuestRun(
            aws_environment,
            {
                "instance": {
                    "network_interfaces_ids": [{"id": eni_id, "device_index": 0}],
                    "assign_public_ip": False,
                    "connection_address": "private",
                    "private_key_path": key,
                    "cloud_init": {"mode": "replace"},
                    "existing_volumes": [{"volume_id": volume_id, "device_name": "/dev/sdf"}],
                    "volumes": [{"device_name": "/dev/sdg", "ebs": {"snapshot_id": snapshot_id}}],
                    "connection_vars": {"ansible_ssh_common_args": proxy},
                }
            },
        )
        consumer.action("create")
        consumer.action("prepare")
        read = """import glob, pathlib, subprocess
path = next(path for path in glob.glob('/dev/disk/by-id/*') if 'VOLUME' in path)
subprocess.run(['mkdir', '-p', '/mnt/reference'], check=True)
subprocess.run(['mount', path, '/mnt/reference'], check=True)
assert pathlib.Path('/mnt/reference/snapshot-marker').read_text() == 'snapshot-storage-ok'
subprocess.run(['umount', '/mnt/reference'], check=True)
""".replace("VOLUME", volume_id.replace("-", ""))
        consumer.play(
            [
                {
                    "name": "Verify snapshot contents over private SSH",
                    "hosts": "molecule",
                    "gather_facts": False,
                    "become": True,
                    "tasks": [
                        {
                            "name": "Read preserved content from the referenced disk",
                            "ansible.builtin.command": {"argv": ["python3", "-c", read]},
                            "changed_when": False,
                        },
                    ],
                }
            ]
        )
        consumer.destroy()
        assert ec2.describe_volumes(VolumeIds=[volume_id])["Volumes"][0]["State"] == "available"
        preserved = ec2.describe_network_interfaces(NetworkInterfaceIds=[eni_id])[
            "NetworkInterfaces"
        ][0]
        assert preserved["Status"] == "available"
        assert preserved["TagSet"] == caller_tags
        assert (
            ec2.describe_snapshots(SnapshotIds=[snapshot_id])["Snapshots"][0]["State"]
            == "completed"
        )
    finally:
        if consumer and (consumer.ephemeral / "aws_run.yml").exists():
            consumer.destroy()
        if eni_id:
            ec2.delete_network_interface(NetworkInterfaceId=eni_id)
        if volume_id:
            ec2.delete_volume(VolumeId=volume_id)
        if snapshot_id:
            ec2.delete_snapshot(SnapshotId=snapshot_id)
        foundation.destroy()
