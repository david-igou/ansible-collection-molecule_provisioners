"""Exercise AWS through the real collection dispatchers inside the test EE."""

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
import requests
import yaml

ROOT = Path(__file__).resolve().parents[3]


class BlockDumper(yaml.SafeDumper):
    """Indent block sequences consistently in generated inventories."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def write_yaml(path, value):
    """Keep test inventories private and readable without flow collections."""
    path.write_text(yaml.dump(value, Dumper=BlockDumper, sort_keys=False, explicit_start=True))
    path.chmod(0o600)


class GuestRun:
    """Keep ownership state on the mounted controller after any cleanup failure."""

    def __init__(self, environment, hosts, defaults=None):
        self.environment = environment
        self.directory = ROOT / ".build/aws-live" / uuid.uuid4().hex
        self.directory.mkdir(mode=0o700, parents=True)
        self.ephemeral = self.directory / "run"
        self.ephemeral.mkdir(mode=0o700)
        (self.ephemeral / "inventory").mkdir(mode=0o700)
        self.inventory = self.directory / "hosts.yml"
        self.sources = {
            "all": {
                "hosts": {
                    "localhost": {
                        "ansible_connection": "local",
                        "ansible_python_interpreter": "/usr/local/bin/python",
                    }
                },
                "children": {
                    "molecule": {
                        "vars": {
                            "mp_backend": "aws",
                            "mp_defaults": {
                                "aws": {
                                    "region": "us-east-2",
                                    "expected_account_id": environment["account"],
                                    "image_id": environment["image"],
                                    "instance_type": "t3.micro",
                                    "management_cidrs": [environment["cidr"]],
                                    **(defaults or {}),
                                }
                            },
                        },
                        "hosts": {host: {"mp": {"aws": value}} for host, value in hosts.items()},
                    }
                },
            }
        }
        write_yaml(self.inventory, self.sources)
        self.extra = self.directory / "vars.json"
        self.extra.write_text(
            json.dumps(
                {
                    "molecule_ephemeral_directory": str(self.ephemeral),
                    "molecule_scenario_name": "aws-live",
                    "mp_aws_wait_timeout": 180,
                    "mp_aws_cleanup_timeout": 180,
                }
            )
        )
        self.extra.chmod(0o600)
        self.run_id = None

    def state(self):
        """Read only the non-secret ownership journal."""
        return yaml.safe_load((self.ephemeral / "aws_run.yml").read_text())

    def runtime(self):
        """Read private runtime hostvars for independent connection checks."""
        return yaml.safe_load((self.ephemeral / "inventory/molecule_runtime.yml").read_text())[
            "all"
        ]["hosts"]

    def play(self, plays, label="verify", check=True, extra_environment=None, check_mode=False):
        """Run Ansible in the EE; suppress all known credentials on failure."""
        path = self.directory / (label + ".yml")
        write_yaml(path, plays)
        environment = dict(os.environ)
        environment.update(extra_environment or {})
        process = subprocess.run(
            [
                "ansible-playbook",
                *(["--check"] if check_mode else []),
                "-i",
                str(self.inventory),
                "-i",
                str(self.ephemeral / "inventory"),
                str(path),
                "-e",
                "@" + str(self.extra),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
            env=environment,
        )
        output = process.stdout + process.stderr
        secrets = [
            os.environ.get(key, "")
            for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
        ]
        runtime_path = self.ephemeral / "inventory/molecule_runtime.yml"
        if runtime_path.exists():
            secrets.extend(host.get("ansible_password", "") for host in self.runtime().values())
        for secret in secrets:
            if secret:
                output = output.replace(secret, "[redacted]")
        log = self.directory / (label + ".log")
        log.write_text(output)
        log.chmod(0o600)
        if check and process.returncode:
            raise AssertionError(
                "AWS lifecycle failed; retained state: "
                + str(self.ephemeral)
                + "\n"
                + "\n".join(output.splitlines()[-65:])
            )
        return process.returncode, output

    def action(self, action, **kwargs):
        """Use the same public import as an ordinary Molecule consumer."""
        return self.play(
            [
                {
                    "name": "Exercise AWS " + action,
                    "ansible.builtin.import_playbook": "david_igou.molecule_provisioners." + action,
                }
            ],
            action,
            **kwargs
        )

    def destroy(self):
        """Retain state on failure and independently verify cloud cleanup on success."""
        if (self.ephemeral / "aws_run.yml").exists():
            self.run_id = self.state()["id"]
        self.action("destroy")
        if self.run_id:
            self.assert_clean()

    def assert_clean(self):
        """Check AWS independently of the destroy task's reported result."""
        ec2 = self.environment["ec2"]
        filters = [{"Name": "tag:molecule-provisioners:run-id", "Values": [self.run_id]}]
        instances = [
            instance
            for reservation in ec2.describe_instances(Filters=filters)["Reservations"]
            for instance in reservation["Instances"]
        ]
        if any(instance["State"]["Name"] != "terminated" for instance in instances):
            raise AssertionError("An owned instance survived cleanup")
        for operation, key in (
            ("describe_vpcs", "Vpcs"),
            ("describe_subnets", "Subnets"),
            ("describe_internet_gateways", "InternetGateways"),
            ("describe_route_tables", "RouteTables"),
            ("describe_security_groups", "SecurityGroups"),
            ("describe_key_pairs", "KeyPairs"),
            ("describe_volumes", "Volumes"),
            ("describe_network_interfaces", "NetworkInterfaces"),
        ):
            if getattr(ec2, operation)(Filters=filters)[key]:
                raise AssertionError("Owned resources survived " + operation)


@pytest.fixture(scope="session")
def aws_environment():
    """Require deliberate opt-in, an expected account and the user's test region."""
    if os.environ.get("RUN_AWS_LIFECYCLE") != "1":
        pytest.skip("Set RUN_AWS_LIFECYCLE=1 to authorize disposable AWS tests")
    expected = os.environ.get("MP_TEST_AWS_ACCOUNT_ID")
    if not expected:
        pytest.fail("Set MP_TEST_AWS_ACCOUNT_ID to the intended disposable test account")
    import boto3

    session = boto3.Session(region_name="us-east-2")
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != expected:
        pytest.fail("AWS identity does not match the explicit expected test account")
    ec2 = session.client("ec2")
    images = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["al2023-ami-2023.*-kernel-6.1-x86_64"]},
            {"Name": "state", "Values": ["available"]},
        ],
    )["Images"]
    image = sorted(images, key=lambda item: (item["CreationDate"], item["ImageId"]), reverse=True)[
        0
    ]["ImageId"]
    cidr = os.environ.get("MP_TEST_AWS_CIDR")
    if not cidr:
        response = requests.get("https://checkip.amazonaws.com", timeout=15)
        response.raise_for_status()
        cidr = response.text.strip() + "/32"
    return {"account": expected, "ec2": ec2, "session": session, "image": image, "cidr": cidr}
