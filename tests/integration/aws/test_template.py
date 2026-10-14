"""Honor a pinned caller-owned template despite a different mutable default."""

import base64
import json

from .conftest import GuestRun


def test_pinned_template_preserves_bootstrap_and_caller_ownership(aws_environment):
    ec2 = aws_environment["ec2"]
    foundation = GuestRun(
        aws_environment,
        {
            "instance": {
                "network_interfaces": [{"device_index": 0, "private_ip_address": "10.77.0.10"}]
            }
        },
    )
    consumer = None
    template_id = None
    try:
        foundation.action("create")
        foundation.action("prepare")
        guest = foundation.state()["hosts"]["instance"]
        bootstrap = json.loads((foundation.ephemeral / "aws-requests/instance.json").read_text())[
            "UserData"
        ]
        template = ec2.create_launch_template(
            LaunchTemplateName="mp-template-test-" + foundation.state()["id"],
            LaunchTemplateData={
                "ImageId": aws_environment["image"],
                "InstanceType": "t3.micro",
                "NetworkInterfaces": [
                    {
                        "DeviceIndex": 0,
                        "PrivateIpAddress": "10.77.0.30",
                        "SubnetId": guest["subnet"]["subnet_id"],
                        "Groups": guest["groups"],
                        "AssociatePublicIpAddress": True,
                    }
                ],
                "UserData": base64.b64encode(bootstrap.encode()).decode(),
            },
        )["LaunchTemplate"]
        template_id = template["LaunchTemplateId"]
        ec2.create_launch_template_version(
            LaunchTemplateId=template_id,
            SourceVersion="1",
            LaunchTemplateData={"UserData": base64.b64encode(b"#!/bin/sh\ntrue\n").decode()},
        )
        ec2.modify_launch_template(LaunchTemplateId=template_id, DefaultVersion="2")
        consumer = GuestRun(
            aws_environment,
            {
                "instance": {
                    "launch_template": {"id": template_id, "version": 1},
                    "private_key_path": str(foundation.ephemeral / "identity_file"),
                }
            },
        )
        consumer.action("create")
        consumer.action("prepare")
        assert consumer.state()["networks"] == {}
        assert consumer.runtime()["instance"]["mp_aws_private_ip"] == "10.77.0.30"
        assert consumer.runtime()["instance"]["mp_aws_subnet_id"] == guest["subnet"]["subnet_id"]
        assert (consumer.ephemeral / "aws-requests/instance.json").stat().st_mode & 0o777 == 0o600
        consumer.destroy()
        assert (
            ec2.describe_launch_templates(LaunchTemplateIds=[template_id])["LaunchTemplates"][0][
                "DefaultVersionNumber"
            ]
            == 2
        )
        assert (foundation.ephemeral / "identity_file").exists()
        foundation.action("prepare")
    finally:
        if consumer and (consumer.ephemeral / "aws_run.yml").exists():
            consumer.destroy()
        if template_id:
            ec2.delete_launch_template(LaunchTemplateId=template_id)
        foundation.destroy()
