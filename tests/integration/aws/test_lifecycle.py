"""Live EC2 behavior and network cleanup through the public provisioner interface."""

import json

import pytest

from .conftest import GuestRun


def test_managed_linux_lifecycle(aws_environment):
    """Create a real guest with no VPC, authenticate, reuse identity and clean everything."""
    run = GuestRun(aws_environment, {"instance": {}})
    try:
        run.action("create")
        first = run.state()
        run.run_id = first["id"]
        assert len(first["networks"]) == 1
        private_request = json.loads((run.ephemeral / "aws-requests/instance.json").read_text())
        if not private_request["UserData"].startswith("#cloud-config\n"):
            raise AssertionError("Cloud-init bootstrap header must end with a real newline")
        run.action("prepare")
        run.play(
            [
                {
                    "name": "Verify authenticated Linux and sudo",
                    "hosts": "molecule",
                    "gather_facts": True,
                    "tasks": [
                        {
                            "name": "Exercise passwordless root access",
                            "ansible.builtin.command": "id -u",
                            "become": True,
                            "register": "root_id",
                            "changed_when": False,
                        },
                        {
                            "name": "Require the expected Linux OS and privileged identity",
                            "ansible.builtin.assert": {
                                "that": [
                                    "ansible_facts['system'] == 'Linux'",
                                    "root_id.stdout == '0'",
                                ]
                            },
                        },
                    ],
                }
            ]
        )
        run.action("create")
        assert (
            run.state()["hosts"]["instance"]["instance_id"]
            == first["hosts"]["instance"]["instance_id"]
        )
        network = first["networks"]["us-east-2"]
        ec2 = aws_environment["ec2"]
        assert ec2.describe_vpc_attribute(VpcId=network["vpc_id"], Attribute="enableDnsSupport")[
            "EnableDnsSupport"
        ]["Value"]
        assert ec2.describe_subnets(SubnetIds=[network["subnet_id"]])["Subnets"][0][
            "MapPublicIpOnLaunch"
        ]
        table = ec2.describe_route_tables(RouteTableIds=[network["route_table_id"]])["RouteTables"][
            0
        ]
        assert any(
            route.get("GatewayId") == network["gateway_id"]
            and route.get("DestinationCidrBlock") == "0.0.0.0/0"
            for route in table["Routes"]
        )
    finally:
        run.destroy()


WINDOWS_BOOTSTRAP = r"""<powershell>
Set-Service -Name WinRM -StartupType Automatic
Start-Service WinRM
$cert = New-SelfSignedCertificate -DnsName $env:COMPUTERNAME -CertStoreLocation Cert:\LocalMachine\My
New-Item -Path WSMan:\LocalHost\Listener -Transport HTTPS -Address * -CertificateThumbPrint $cert.Thumbprint -Force
New-NetFirewallRule -Name WinRM-HTTPS -DisplayName WinRM-HTTPS -Enabled True -Direction Inbound -Protocol TCP -LocalPort 5986 -Action Allow
</powershell>"""


@pytest.mark.parametrize("connection", ["winrm", "psrp"])
def test_windows_authenticated_transports_and_explicit_password(aws_environment, connection):
    """Decrypt the actual Administrator password and authenticate both Windows plugins."""
    images = aws_environment["ec2"].describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["Windows_Server-2022-English-Full-Base-*"]},
            {"Name": "state", "Values": ["available"]},
        ],
    )["Images"]
    image = sorted(images, key=lambda value: value["CreationDate"], reverse=True)[0]["ImageId"]
    options = (
        {"ansible_winrm_transport": "ntlm", "ansible_winrm_server_cert_validation": "ignore"}
        if connection == "winrm"
        else {"ansible_psrp_auth": "ntlm", "ansible_psrp_cert_validation": "ignore"}
    )
    run = GuestRun(
        aws_environment,
        {
            "instance": {
                "image_id": image,
                "instance_type": "t3.medium",
                "connection": connection,
                "key_inject_method": "ec2",
                "cloud_init": {"enabled": False},
                "user_data": WINDOWS_BOOTSTRAP,
                "connection_vars": options,
            }
        },
    )
    try:
        run.action("create")
        run.run_id = run.state()["id"]
        run.action("prepare")
        run.play(
            [
                {
                    "name": "Authenticate Windows and execute PowerShell",
                    "hosts": "molecule",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "Exercise Windows module execution",
                            "ansible.windows.win_ping": {},
                            "register": "windows_ping",
                        },
                        {
                            "name": "Read authenticated Windows identity",
                            "ansible.windows.win_whoami": {},
                            "register": "windows_identity",
                        },
                        {
                            "name": "Require Administrator access",
                            "ansible.builtin.assert": {
                                "that": [
                                    "windows_ping.ping == 'pong'",
                                    "windows_identity.account.account_name | lower == 'administrator'",
                                ]
                            },
                        },
                    ],
                }
            ]
        )
        # Authentication values can change without changing immutable launch intent.
        from .conftest import write_yaml

        host = run.sources["all"]["children"]["molecule"]["hosts"]["instance"]["mp"]["aws"]
        host["connection_vars"]["ansible_password"] = run.runtime()["instance"]["ansible_password"]
        write_yaml(run.inventory, run.sources)
        run.action("create")
        run.action("prepare")
    finally:
        run.destroy()


def test_existing_network_references_and_removed_inventory(aws_environment):
    """Use an existing live network and key without changing caller resources."""
    foundation = GuestRun(aws_environment, {"instance": {}})
    consumer = None
    try:
        foundation.action("create")
        foundation.run_id = foundation.state()["id"]
        network = foundation.state()["networks"]["us-east-2"]
        ec2 = aws_environment["ec2"]
        before_subnet = ec2.describe_subnets(SubnetIds=[network["subnet_id"]])["Subnets"][0]
        groups = foundation.state()["hosts"]["instance"]["groups"]
        consumer = GuestRun(
            aws_environment,
            {
                "instance": {
                    "network": {"mode": "existing"},
                    "subnet_id": network["subnet_id"],
                    "security_groups": groups,
                    "private_key_path": str(foundation.ephemeral / "identity_file"),
                    "cloud_init": {"enabled": True},
                }
            },
        )
        consumer.action("create")
        consumer.run_id = consumer.state()["id"]
        assert consumer.state()["networks"] == {}
        consumer.action("prepare")
        assert consumer.runtime()["instance"]["mp_aws_vpc_id"] == network["vpc_id"]
        from .conftest import write_yaml

        consumer.sources["all"]["children"]["molecule"]["hosts"] = {"cleanup-placeholder": {}}
        write_yaml(consumer.inventory, consumer.sources)
        consumer.destroy()
        assert (foundation.ephemeral / "identity_file").exists()
        after_subnet = ec2.describe_subnets(SubnetIds=[network["subnet_id"]])["Subnets"][0]
        assert before_subnet == after_subnet
        foundation.action("prepare")
    finally:
        if consumer is not None and (consumer.ephemeral / "aws_run.yml").exists():
            consumer.destroy()
        foundation.destroy()


def test_overlapping_run_isolation_and_shared_network(aws_environment):
    """Two identical logical host names must never share managed cloud resources."""
    first = GuestRun(aws_environment, {"instance": {}})
    second = GuestRun(aws_environment, {"instance": {}})
    try:
        first.action("create")
        first.run_id = first.state()["id"]
        second.action("create")
        second.run_id = second.state()["id"]
        assert (
            first.state()["networks"]["us-east-2"]["vpc_id"]
            != second.state()["networks"]["us-east-2"]["vpc_id"]
        )
        assert (
            first.runtime()["instance"]["mp_aws_instance_id"]
            != second.runtime()["instance"]["mp_aws_instance_id"]
        )
        first.destroy()
        second.action("prepare")
    finally:
        if (first.ephemeral / "aws_run.yml").exists():
            first.destroy()
        second.destroy()
