"""Interrupt real controllers at API boundaries and recover the saved ownership."""

from pathlib import Path

import pytest

from .conftest import GuestRun


@pytest.mark.parametrize("stage", ["vpc", "subnet", "gateway", "route", "group", "key", "launch"])
def test_interrupted_create_recovers_without_duplicate_resources(aws_environment, stage):
    settings = {"key_inject_method": "ec2", "ssh_user": "ec2-user"} if stage == "key" else {}
    hosts = {"instance": settings, "peer": {}} if stage == "launch" else {"instance": settings}
    run = GuestRun(aws_environment, hosts)
    task = {
        "group": "AWS | Create a run-owned security group",
        "key": "AWS | Import the owned public EC2 key",
        "launch": "AWS | Launch exactly one guest with the saved token",
    }.get(stage, "AWS | Provision the owned " + stage)
    try:
        code, _output = run.action(
            "create",
            check=False,
            extra_environment={
                "ANSIBLE_CALLBACK_PLUGINS": str(Path(__file__).parent / "callback_plugins"),
                "ANSIBLE_CALLBACKS_ENABLED": "interrupt",
                "MP_TEST_AWS_INTERRUPT_TASK": task,
            },
        )
        assert code == 97, "The controller did not stop at the requested API boundary"
        original = run.state()
        run.run_id = original["id"]
        if stage in ("subnet", "launch"):
            run.action("create")
            assert run.state()["id"] == original["id"]
            run.action("prepare")
            filters = [{"Name": "tag:molecule-provisioners:run-id", "Values": [run.run_id]}]
            guests = aws_environment["ec2"].describe_instances(Filters=filters)["Reservations"]
            assert sum(len(reservation["Instances"]) for reservation in guests) == len(hosts)
            assert len(run.state()["networks"]) == 1
    finally:
        run.destroy()


def test_check_mode_and_ownership_mismatch_preserve_resources(aws_environment):
    run = GuestRun(aws_environment, {"instance": {}})
    try:
        run.action("create", check_mode=True)
        assert not (run.ephemeral / "aws_run.yml").exists()
        run.action("create")
        original = run.state()
        run.run_id = original["id"]
        instance_id = original["hosts"]["instance"]["instance_id"]
        journal = (run.ephemeral / "aws_run.yml").read_bytes()
        run.action("destroy", check_mode=True)
        assert (run.ephemeral / "aws_run.yml").read_bytes() == journal
        ec2 = aws_environment["ec2"]
        assert (
            ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0][
                "State"
            ]["Name"]
            == "running"
        )
        ec2.delete_tags(Resources=[instance_id], Tags=[{"Key": "molecule-provisioners:run-id"}])
        try:
            code, _output = run.action("destroy", check=False)
            assert code != 0
            assert (run.ephemeral / "aws_run.yml").exists()
            assert (
                ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][
                    0
                ]["State"]["Name"]
                == "running"
            )
        finally:
            ec2.create_tags(
                Resources=[instance_id],
                Tags=[{"Key": "molecule-provisioners:run-id", "Value": run.run_id}],
            )
    finally:
        run.destroy()
