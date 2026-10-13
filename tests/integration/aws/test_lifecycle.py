"""Exercise public EC2 lifecycle playbooks against a caller-owned subnet."""

import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AWS_LIFECYCLE") != "1",
    reason="Set RUN_AWS_LIFECYCLE=1 and the documented AWS network/image inputs.",
)


def test_linux_guests_resume_and_cleanup(tmp_path: Path) -> None:
    """Create twice, connect to both releases, and remove guests without deleting the network."""
    import boto3

    deployment = os.environ["MOLECULE_AWS_DEPLOYMENT_ID"]
    region = os.environ["AWS_REGION"]
    subnet = os.environ["MOLECULE_AWS_SUBNET_ID"]
    ec2 = boto3.client("ec2", region_name=region)
    inventory_dir = tmp_path / "inventory"
    inventory_dir.mkdir()
    inventory = {
        "all": {
            "vars": {
                "molecule_ephemeral_directory": str(tmp_path),
                "molecule_scenario_name": "aws-lifecycle",
            },
            "hosts": {
                "localhost": {
                    "ansible_connection": "local",
                    "ansible_python_interpreter": sys.executable,
                },
            },
            "children": {
                "molecule": {
                    "vars": {"mp_backend": "aws"},
                    "hosts": {
                        "rhel" + major: {
                            "mp": {
                                "aws": {
                                    "image_id": os.environ["MOLECULE_AWS_RHEL" + major + "_AMI_ID"],
                                },
                            },
                            "expected_major": major,
                        }
                        for major in ("9", "10")
                    },
                },
            },
        },
    }
    source = tmp_path / "source.yml"
    source.write_text(yaml.safe_dump(inventory), encoding="utf-8")

    def run(action: str) -> None:
        playbook = tmp_path / (action + ".yml")
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "name": "Exercise AWS " + action,
                        "ansible.builtin.import_playbook": "david_igou.molecule_provisioners."
                        + action,
                    },
                ]
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            ["ansible-playbook", "-i", str(source), "-i", str(inventory_dir), str(playbook)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    try:
        run("create")
        saved = yaml.safe_load((tmp_path / "aws_run.yml").read_text(encoding="utf-8"))
        assert saved["prefix"].endswith(saved["id"])
        run("create")
        assert yaml.safe_load((tmp_path / "aws_run.yml").read_text(encoding="utf-8")) == saved
        selected = [
            {"Name": "tag:demojam:deployment", "Values": [deployment]},
            {"Name": "tag:demojam:purpose", "Values": ["molecule"]},
            {"Name": "tag:molecule_run_id", "Values": [saved["id"]]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
        guests = [
            i
            for r in ec2.describe_instances(Filters=selected)["Reservations"]
            for i in r["Instances"]
        ]
        assert len(guests) == 2
        volumes = [b["Ebs"]["VolumeId"] for i in guests for b in i["BlockDeviceMappings"]]
        assert all(
            b["Ebs"]["DeleteOnTermination"] for i in guests for b in i["BlockDeviceMappings"]
        )
        assert all(
            v["Encrypted"] and v["VolumeType"] == "gp3"
            for v in ec2.describe_volumes(VolumeIds=volumes)["Volumes"]
        )
        run("prepare")
        verify = tmp_path / "verify.yml"
        verify.write_text(
            yaml.safe_dump(
                [
                    {
                        "name": "Verify both RHEL releases over SSH",
                        "hosts": "molecule",
                        "gather_facts": True,
                        "tasks": [
                            {
                                "name": "Require the selected guest operating system",
                                "ansible.builtin.assert": {
                                    "that": [
                                        "ansible_facts['distribution'] == 'RedHat'",
                                        "ansible_facts['distribution_major_version'] == expected_major",
                                    ],
                                },
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            ["ansible-playbook", "-i", str(source), "-i", str(inventory_dir), str(verify)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        run("destroy")

    assert not (tmp_path / "aws_run.yml").exists()
    assert not ec2.describe_instances(Filters=selected)["Reservations"]
    assert not ec2.describe_volumes(Filters=[{"Name": "volume-id", "Values": volumes}])["Volumes"]
    assert not ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [saved["prefix"]]}]
    )["SecurityGroups"]
    assert not ec2.describe_key_pairs(Filters=[{"Name": "key-name", "Values": [saved["prefix"]]}])[
        "KeyPairs"
    ]
    assert ec2.describe_subnets(SubnetIds=[subnet])["Subnets"][0]["State"] == "available"
