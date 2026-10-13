#!/usr/bin/python
# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
# GNU General Public License v3.0+ (https://www.gnu.org/licenses/gpl-3.0.txt).
# Alternatively, use under the MIT license in LICENSE.
"""Read AWS identity and launch one instance with a persisted idempotency token."""

DOCUMENTATION = r"""
---
module: aws_instance
short_description: Launch a disposable EC2 instance with a stable client token
description:
  - Internal launch helper for the AWS role; network and cleanup orchestration remain in the role.
  - Persists private launch requests before contacting AWS so an interrupted launch can be replayed.
  - Requires boto3 and botocore on the controller.
author:
  - David Igou (@david-igou)
version_added: 0.0.7
options:
  operation:
    description: Read identity, inspect a pinned template, launch a guest, or save private console diagnostics.
    type: str
    choices:
      - identity
      - template
      - launch
      - diagnostics
    default: launch
  region:
    description: AWS region, falling back to AWS_REGION or the SDK configuration.
    type: str
  profile:
    description: Optional SDK profile; otherwise the SDK credential chain is used.
    type: str
  instance_type:
    description: Optional instance type for read-only architecture, NIC limit and AZ eligibility checks.
    type: str
  directory:
    description: Private Molecule ephemeral directory for launch replay files.
    type: path
  host:
    description: Logical inventory host with a safe ASCII name.
    type: str
  run_id:
    description: Saved random run identity used to derive the EC2 client token.
    type: str
  instance_id:
    description: Owned instance ID to inspect for private diagnostics.
    type: str
  request:
    description: Role-rendered RunInstances arguments, excluding counts and client token.
    type: dict
    default: {}
  user_data:
    description: Sensitive bootstrap payload supplied separately to preserve non-secret numeric resource IDs.
    type: str
  template:
    description: Existing template ID or name and a numeric version.
    type: dict
    default: {}
attributes:
  check_mode:
    description: Performs discovery without launching instances or writing private replay files.
    support: full
"""
EXAMPLES = r"""
- name: Check the effective AWS account before provisioning
  david_igou.molecule_provisioners.aws_instance:
    operation: identity
    region: us-east-2
  register: aws_identity
"""
RETURN = r"""
account:
  description: Effective AWS account ID.
  type: str
  returned: when operation is identity
partition:
  description: AWS partition.
  type: str
  returned: when operation is identity
region:
  description: Resolved SDK region.
  type: str
  returned: always
instance_id:
  description: Launched instance ID.
  type: str
  returned: when operation is launch outside check mode
console_path:
  description: Private bounded console log path.
  type: str
  returned: when operation is diagnostics
console_tail:
  description: Sensitive console tail; consuming tasks must suppress output unless explicitly opted in.
  type: str
  returned: when operation is diagnostics
statuses:
  description: EC2 instance and system status checks.
  type: list
  elements: dict
  returned: when operation is diagnostics
architectures:
  description: Instance type supported CPU architectures.
  type: list
  elements: str
  returned: when identity includes instance_type
max_interfaces:
  description: Instance type network interface limit.
  type: int
  returned: when identity includes instance_type
zones:
  description: Eligible enabled availability zones.
  type: list
  elements: str
  returned: when identity includes instance_type
template:
  description: Sensitive effective launch-template data; suppress the consuming task output.
  type: dict
  returned: when operation is template
"""

import base64
import json
import os
import re
from pathlib import Path

from ansible.module_utils.basic import AnsibleModule, missing_required_lib
from ansible_collections.david_igou.molecule_provisioners.plugins.module_utils.aws_spec import (
    token,
    owned,
    valid_request,
)

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    HAS_BOTO = True
except ImportError:
    HAS_BOTO = False


def save_request(directory, host, request):
    """Atomically save a private replay body and refuse changed launch parameters."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", host):
        raise ValueError("Invalid logical host name")
    target = Path(directory) / "aws-requests"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = target / (host + ".json")
    if path.exists():
        if json.loads(path.read_text()) != request:
            raise ValueError(
                "Saved launch request changed; destroy the run before changing launch configuration"
            )
        return False
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(request, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return True


def launch(client, request, run_id, host):
    """Use exactly one stable SDK token; never fall back to unscoped matching."""
    if set(request) & {"ClientToken", "MinCount", "MaxCount", "DryRun"}:
        raise ValueError("Launch token and instance counts are reserved")
    response = client.run_instances(
        **request, ClientToken=token(run_id, host), MinCount=1, MaxCount=1
    )
    instances = response["Instances"]
    if len(instances) != 1 or instances[0]["State"]["Name"] in ("shutting-down", "terminated"):
        raise ValueError(
            "Saved launch no longer represents a live guest; destroy the run before recreating"
        )
    return instances[0]["InstanceId"]


def main():
    """Dispatch read-only discovery or a guarded launch without logging user data."""
    module = AnsibleModule(
        argument_spec={
            "operation": {
                "type": "str",
                "choices": ["identity", "template", "launch", "diagnostics"],
                "default": "launch",
            },
            "region": {"type": "str"},
            "profile": {"type": "str"},
            "instance_type": {"type": "str"},
            "instance_id": {"type": "str"},
            "directory": {"type": "path"},
            "host": {"type": "str"},
            "run_id": {"type": "str"},
            "request": {"type": "dict", "default": {}},
            "user_data": {"type": "str", "no_log": True},
            "template": {"type": "dict", "default": {}},
        },
        required_if=[
            ("operation", "launch", ["directory", "host", "run_id"]),
            ("operation", "diagnostics", ["directory", "host", "run_id", "instance_id"]),
        ],
        supports_check_mode=True,
    )
    if not HAS_BOTO:
        module.fail_json(msg=missing_required_lib("boto3 and botocore"))
    try:
        session = boto3.Session(profile_name=module.params["profile"])
        region = module.params["region"] or os.environ.get("AWS_REGION") or session.region_name
        if not region:
            module.fail_json(msg="Supply region or configure AWS_REGION/the SDK default region")
        config = Config(retries={"mode": "standard", "max_attempts": 8})
        if module.params["operation"] == "identity":
            identity = session.client(
                "sts", region_name=region, config=config
            ).get_caller_identity()
            eligibility = {}
            if module.params["instance_type"]:
                ec2 = session.client("ec2", region_name=region, config=config)
                kind = module.params["instance_type"]
                info = ec2.describe_instance_types(InstanceTypes=[kind])["InstanceTypes"][0]
                offerings = ec2.describe_instance_type_offerings(
                    LocationType="availability-zone",
                    Filters=[{"Name": "instance-type", "Values": [kind]}],
                )["InstanceTypeOfferings"]
                enabled = ec2.describe_availability_zones(
                    Filters=[{"Name": "state", "Values": ["available"]}]
                )["AvailabilityZones"]
                zones = sorted(
                    {zone["ZoneName"] for zone in enabled}
                    & {offering["Location"] for offering in offerings}
                )
                eligibility = {
                    "architectures": info["ProcessorInfo"]["SupportedArchitectures"],
                    "max_interfaces": info["NetworkInfo"]["MaximumNetworkInterfaces"],
                    "zones": zones,
                }
            module.exit_json(
                changed=False,
                account=identity["Account"],
                partition=identity["Arn"].split(":")[1],
                region=region,
                **eligibility
            )
        client = session.client("ec2", region_name=region, config=config)
        if module.params["operation"] == "template":
            template = module.params["template"]
            args = {
                "LaunchTemplateId" if template.get("id") else "LaunchTemplateName": template.get(
                    "id"
                )
                or template.get("name"),
                "Versions": [str(template["version"])],
            }
            data = client.describe_launch_template_versions(**args)["LaunchTemplateVersions"][0][
                "LaunchTemplateData"
            ]
            if data.get("UserData"):
                data["UserData"] = base64.b64decode(data["UserData"]).decode("utf-8")
            module.exit_json(changed=False, template=data, region=region)
        if module.params["operation"] == "diagnostics":
            instance_id = module.params["instance_id"]
            reservations = client.describe_instances(InstanceIds=[instance_id])["Reservations"]
            guests = [guest for reservation in reservations for guest in reservation["Instances"]]
            owned([{"tags": guest.get("Tags", [])} for guest in guests], module.params["run_id"])
            statuses = client.describe_instance_status(
                InstanceIds=[instance_id], IncludeAllInstances=True
            )["InstanceStatuses"]
            encoded = client.get_console_output(InstanceId=instance_id, Latest=True).get(
                "Output", ""
            )
            console = base64.b64decode(encoded).decode("utf-8", errors="replace")[-262144:]
            host = module.params["host"]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", host):
                raise ValueError("Invalid diagnostic host name")
            path = Path(module.params["directory"]) / (host + "-console.log")
            if not module.check_mode:
                with os.fdopen(
                    os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
                ) as stream:
                    stream.write(console)
            module.exit_json(
                changed=False,
                region=region,
                console_path=str(path),
                console_tail="\n".join(console.splitlines()[-40:])[-8192:],
                statuses=statuses,
            )
        if module.check_mode:
            module.exit_json(changed=True, region=region)
        request = dict(module.params["request"])
        if "UserData" in request:
            raise ValueError(
                "Supply sensitive bootstrap through user_data rather than the request mapping"
            )
        if module.params["user_data"] is not None:
            request["UserData"] = module.params["user_data"]
        valid_request(request)
        first = save_request(module.params["directory"], module.params["host"], request)
        instance_id = launch(client, request, module.params["run_id"], module.params["host"])
        module.exit_json(changed=first, instance_id=instance_id, region=region)
    except ClientError as error:
        module.fail_json(
            msg="AWS operation failed: "
            + error.response["Error"]["Code"]
            + "; retained run state can be used for cleanup"
        )
    except (BotoCoreError, ValueError, OSError, KeyError) as error:
        module.fail_json(
            msg=(
                str(error)
                if isinstance(error, ValueError)
                else "AWS discovery/launch failed; inspect credentials, network and saved run state"
            )
        )


if __name__ == "__main__":
    main()
