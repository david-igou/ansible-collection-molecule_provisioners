"""AWS inventory, bootstrap, ownership and idempotent launch regressions."""

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    module_spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


spec = load("aws_spec", "plugins/module_utils/aws_spec.py")
with patch.dict(
    sys.modules,
    {"ansible_collections.david_igou.molecule_provisioners.plugins.module_utils.aws_spec": spec},
):
    instance = load("aws_instance", "plugins/modules/aws_instance.py")
lock = load("aws_lock", "plugins/action/aws_lock.py")


def guest(**changes):
    return spec.normalize(
        {
            "instance": {
                "region": "us-east-2",
                "image_id": "ami-0123456789abcdef0",
                "management_cidrs": ["198.51.100.1/32"],
                **changes,
            }
        }
    )["instance"]


def image(**changes):
    return {
        "image_id": "ami-0123456789abcdef0",
        "root_device_name": "/dev/xvda",
        "block_device_mappings": [{"device_name": "/dev/xvda", "ebs": {"volume_size": 8}}],
        **changes,
    }


def request(**changes):
    return spec.request(
        guest(**changes),
        image(),
        "subnet-0123456789abcdef0",
        ["sg-0123456789abcdef0"],
        "a" * 32,
        "instance",
        "#cloud-config\n",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"typo": True},
        {"ssh_port": True},
        {"ssh_port": 0},
        {"assign_public_ip": "true"},
        {"assign_public_ip": False},
        {"connection": "unknown"},
        {"connection_vars": {"ansible_host": "other"}},
        {"connection_vars": {"password": "dummy"}},
        {"image_filters": {"name": "other"}},
        {"network": {"mode": "typo"}},
        {"network": {"mode": "create"}, "subnet_id": "subnet-other"},
        {"network": {"vpc_cidr": "10.0.0.1/24"}},
        {"network": {"subnet_cidr": "10.78.0.0/24"}},
        {"root_disk": {"size": -1}},
        {"root_disk": {"unrecognized": True}},
        {"cloud_init": {"enabled": False}},
        {"cloud_init": {"mode": "unknown"}},
        {"user_data": "secret-bootstrap"},
        {"tags": {spec.RUN: "other"}},
        {"instance_options": {"count": 2}},
        {"instance_options": {"disable_api_termination": True}},
        {"connection": "network_cli"},
        {"application_ports": [{"name": "api", "port": 80, "protocol": "SCTP"}]},
        {"application_ports": [{"name": "api", "port": 80}, {"name": "api", "port": 81}]},
        {"volumes": [{"device_name": "/dev/sdf", "ebs": {"delete_on_termination": False}}]},
        {"launch_template": {"name": "template", "version": "$Latest"}},
    ],
)
def test_reject_invalid_inventory(changes):
    with pytest.raises(ValueError):
        guest(**changes)


def test_recursive_defaults_replace_lists_and_keep_transport_options():
    result = spec.normalize(
        {"one": {"cloud_init": {"user_data": {"packages": ["curl"]}}}},
        {
            "image_id": "ami-example",
            "management_cidrs": ["198.51.100.1/32"],
            "cloud_init": {"user_data": {"packages": ["discarded"], "timezone": "UTC"}},
        },
    )["one"]
    assert result["cloud_init"]["user_data"] == {"packages": ["curl"], "timezone": "UTC"}
    assert result["network"]["mode"] == "create"
    assert guest(subnet_id="subnet-existing")["network"]["mode"] == "existing"


def test_bootstrap_preserves_other_users_keys_and_package_requests():
    configured = guest(
        cloud_init={
            "user_data": {
                "users": [
                    "default",
                    {"name": "cloud-user", "sudo": False, "ssh_authorized_keys": ["caller"]},
                ],
                "packages": ["curl"],
            }
        }
    )
    data = spec.cloud_data(configured, "injected")
    assert data["users"][0] == "default"
    assert data["users"][1]["sudo"] is False
    assert data["users"][1]["ssh_authorized_keys"] == ["caller", "injected"]
    assert data["packages"] == ["curl", "python3", "sudo"]
    assert spec.cloud_data(configured, "injected") == data


def test_opaque_and_disabled_bootstrap_and_appliance_access():
    assert spec.cloud_data(guest(key_inject_method="ec2", user_data="opaque")) == "opaque"
    assert spec.cloud_data(guest(key_inject_method="none", cloud_init={"enabled": False})) == ""
    appliance = guest(
        connection="network_cli",
        connection_probe="/system identity print",
        connection_vars={"ansible_network_os": "community.routeros.routeros"},
    )
    assert appliance["key_inject_method"] == "none"
    assert not appliance["cloud_init"]["enabled"]


def test_manifest_intent_excludes_passwords_and_user_data():
    configured = guest(
        connection_vars={"ansible_password": "dummy-private"},
        cloud_init={"user_data": {"write_files": [{"content": "dummy-bootstrap"}]}},
    )
    saved = json.dumps(spec.intent({"host": configured}))
    assert "dummy-private" not in saved
    assert "dummy-bootstrap" not in saved


def test_launch_shape_and_disposable_ownership():
    data = request(
        volumes=[{"device_name": "/dev/sdf", "ebs": {"volume_size": 2}}],
        instance_options={
            "cpu_options": {"core_count": 1, "threads_per_core": 2},
            "metadata_options": {"http_tokens": "required"},
        },
    )
    assert spec.valid_request(data)
    assert data["NetworkInterfaces"][0]["AssociatePublicIpAddress"]
    assert data["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"]
    assert data["BlockDeviceMappings"][1]["Ebs"]["Encrypted"]
    assert data["CpuOptions"] == {"CoreCount": 1, "ThreadsPerCore": 2}
    assert {item["ResourceType"] for item in data["TagSpecifications"]} == {
        "instance",
        "volume",
        "network-interface",
    }


def test_root_growth_and_advanced_settings():
    with pytest.raises(ValueError, match="shrink"):
        request(root_disk={"size": 2})
    data = request(
        root_disk={"size": 20, "iops": 3000, "kms_key_id": "example-key"},
        iam_instance_profile="example-profile",
        instance_options={"cpu_credit_specification": "standard"},
    )
    assert data["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] == 20
    assert data["BlockDeviceMappings"][0]["Ebs"]["KmsKeyId"] == "example-key"
    assert data["IamInstanceProfile"] == {"Name": "example-profile"}
    assert data["CreditSpecification"] == {"CpuCredits": "standard"}


def test_referenced_network_interfaces_are_not_tagged_or_deleted():
    data = request(
        network_interfaces_ids=[{"id": "eni-0123456789abcdef0", "device_index": 0}],
        assign_public_ip=False,
        connection_address="private",
    )
    assert data["NetworkInterfaces"] == [
        {
            "NetworkInterfaceId": "eni-0123456789abcdef0",
            "DeviceIndex": 0,
            "DeleteOnTermination": False,
        }
    ]
    assert "network-interface" not in [item["ResourceType"] for item in data["TagSpecifications"]]


def test_shared_regional_networks_require_compatible_settings_and_az():
    one, two = guest(), guest()
    plans = {"one": {"zones": ["us-east-2a", "us-east-2b"]}, "two": {"zones": ["us-east-2b"]}}
    assert spec.network_plan({"one": one, "two": two}, plans)["us-east-2"]["az"] == "us-east-2b"
    two["network"]["vpc_cidr"] = "10.80.0.0/24"
    with pytest.raises(ValueError, match="matching"):
        spec.network_plan({"one": one, "two": two}, plans)


def test_image_selection_and_architecture_validation():
    images = [
        dict(
            image(),
            creation_date="2026-10-13",
            state="available",
            root_device_type="ebs",
            architecture="x86_64",
        )
    ]
    assert spec.image_info(images, ["x86_64"])["image_id"] == images[0]["image_id"]
    with pytest.raises(ValueError, match="compatible"):
        spec.image_info(images, ["arm64"])


def test_ownership_is_mandatory_and_network_reconciliation_is_conservative():
    owned = {"vpc_id": "vpc-one", "tags": spec.tags("a" * 32)}
    result = spec.reconcile_network({"pending": "vpc"}, {"vpc": [owned]}, "a" * 32)
    assert result["vpc_id"] == "vpc-one"
    assert result["pending"] == ""
    with pytest.raises(ValueError, match="Uncertain"):
        spec.reconcile_network({"pending": "vpc"}, {"vpc": []}, "a" * 32)
    with pytest.raises(ValueError, match="ownership"):
        spec.owned([owned], "b" * 32)
    assert (
        spec.reconcile_network({"vpc_id": "vpc-one"}, {"vpc": []}, "a" * 32, deleting=True)[
            "vpc_id"
        ]
        == "vpc-one"
    )


def test_replay_requests_are_private_and_immutable(tmp_path):
    assert instance.save_request(tmp_path, "guest", request())
    assert not instance.save_request(tmp_path, "guest", request())
    assert (tmp_path / "aws-requests/guest.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="changed"):
        instance.save_request(tmp_path, "guest", request(instance_type="t3.small"))
    assert spec.token("a" * 32, "guest") != spec.token("b" * 32, "guest")


def test_lost_launch_response_reuses_the_same_client_token():
    client = Mock()
    client.run_instances.side_effect = [
        TimeoutError("response lost"),
        {"Instances": [{"InstanceId": "i-example", "State": {"Name": "pending"}}]},
    ]
    with pytest.raises(TimeoutError):
        instance.launch(client, request(), "a" * 32, "guest")
    assert instance.launch(client, request(), "a" * 32, "guest") == "i-example"
    calls = client.run_instances.call_args_list
    assert calls[0].kwargs["ClientToken"] == calls[1].kwargs["ClientToken"]
    assert calls[1].kwargs["MinCount"] == calls[1].kwargs["MaxCount"] == 1


def test_sensitive_bootstrap_does_not_corrupt_returned_resource_ids(tmp_path):
    """Exercise Ansible's real argument sanitization, including request integers."""
    from ansible.module_utils.common.parameters import _list_no_log_values, remove_values

    data = request()
    data.pop("UserData")
    arguments = {
        "operation": "launch",
        "directory": str(tmp_path),
        "host": "instance",
        "run_id": "a" * 32,
        "region": "us-east-2",
        "request": data,
        "user_data": "dummy-private-bootstrap",
    }
    client = Mock()
    client.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-0123456789abcdef0", "State": {"Name": "pending"}}]
    }
    session = Mock()
    session.client.return_value = client
    module = Mock(params=arguments, check_mode=False)
    module.params.update(profile=None, template={})
    module.exit_json.side_effect = SystemExit(0)
    with patch.object(instance, "AnsibleModule", return_value=module) as constructor, patch.object(
        instance.boto3, "Session", return_value=session
    ), pytest.raises(SystemExit) as exited:
        instance.main()
    assert exited.value.code == 0
    redactions = _list_no_log_values(constructor.call_args.kwargs["argument_spec"], arguments)
    result = remove_values(module.exit_json.call_args.kwargs, redactions)
    assert result["instance_id"] == "i-0123456789abcdef0"
    assert (
        remove_values("dummy-private-bootstrap", redactions)
        == "VALUE_SPECIFIED_IN_NO_LOG_PARAMETER"
    )


def test_controller_lease_rejects_other_live_processes_and_recovers_stale_ones(tmp_path):
    lock.lease(tmp_path, "held", os.getpid())
    with pytest.raises(ValueError, match="Another active"):
        lock.lease(tmp_path, "held", os.getppid())
    lock.lease(tmp_path, "released", os.getpid())
    (tmp_path / ".aws-lock").write_text(
        json.dumps({"pid": 99999999, "start": "old", "namespace": os.readlink("/proc/self/ns/pid")})
    )
    lock.lease(tmp_path, "held", os.getpid())
    lock.lease(tmp_path, "released", os.getpid())


def test_pinned_template_bootstrap_compute_and_existing_network():
    configured = guest(launch_template={"name": "test", "version": 1})
    resolved = spec.template_spec(
        configured,
        {
            "ImageId": "ami-other",
            "InstanceType": "t3.small",
            "SubnetId": "subnet-existing",
            "UserData": "opaque",
        },
    )
    assert resolved["instance_type"] == "t3.small"
    assert resolved["network"]["mode"] == "existing"
    assert resolved["user_data"] == "opaque"
    assert not resolved["cloud_init"]["enabled"]
    with pytest.raises(ValueError, match="termination protection"):
        spec.template_spec(configured, {"DisableApiTermination": True})


def test_public_multi_nic_launch_defers_secondary_attachment():
    data = request(network_interfaces=[{"device_index": 1}, {"device_index": 0}])
    assert [nic["DeviceIndex"] for nic in data["NetworkInterfaces"]] == [0]
    assert data["NetworkInterfaces"][0]["AssociatePublicIpAddress"]
    assert spec.valid_request(data)
    private = request(
        network_interfaces=[{"device_index": 0}, {"device_index": 1}],
        assign_public_ip=False,
        connection_address="private",
    )
    assert len(private["NetworkInterfaces"]) == 2


@pytest.mark.parametrize(
    "configuration",
    [
        {"network_interfaces": [{"device_index": 1}]},
        {"network_interfaces": [{"device_index": 0}, {"device_index": 0}]},
        {"network_interfaces_ids": [{"id": "eni-existing"}]},
        {"user_data": {"opaque": "must-be-a-string"}, "key_inject_method": "ec2"},
        {"network_interfaces": [{"device_index": 0, "groups": "sg-invalid"}]},
    ],
)
def test_invalid_devices_and_opaque_payloads_fail_before_mutation(configuration):
    with pytest.raises(ValueError):
        guest(**configuration)


def test_network_reconciliation_refuses_moved_or_changed_resources():
    run = "a" * 32
    saved = {
        "vpc_id": "vpc-one",
        "vpc_cidr": "10.77.0.0/24",
        "subnet_id": "subnet-one",
        "subnet_cidr": "10.77.0.0/24",
        "az": "us-east-2a",
    }
    with pytest.raises(ValueError, match="CIDR"):
        spec.reconcile_network(
            saved,
            {"vpc": [{"vpc_id": "vpc-one", "cidr_block": "10.88.0.0/24", "tags": spec.tags(run)}]},
            run,
        )
    with pytest.raises(ValueError, match="different VPC"):
        spec.reconcile_network(
            saved,
            {
                "subnet": [
                    {"subnet_id": "subnet-one", "vpc_id": "vpc-other", "tags": spec.tags(run)}
                ]
            },
            run,
        )
    with pytest.raises(ValueError, match="unexpected VPC"):
        spec.reconcile_network(
            saved,
            {
                "gateway": [
                    {
                        "internet_gateway_id": "igw-one",
                        "attachments": [{"vpc_id": "vpc-other"}],
                        "tags": spec.tags(run),
                    }
                ]
            },
            run,
        )


def test_explicit_create_does_not_adopt_template_network():
    configured = guest(launch_template={"name": "test", "version": 1}, network={"mode": "create"})
    with pytest.raises(ValueError, match="conflicts"):
        spec.template_spec(
            configured,
            {
                "ImageId": image()["image_id"],
                "NetworkInterfaces": [{"DeviceIndex": 0, "SubnetId": "subnet-existing"}],
            },
        )
