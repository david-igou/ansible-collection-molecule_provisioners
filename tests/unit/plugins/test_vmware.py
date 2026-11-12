"""VMware parity validation, ownership, bootstrap and runtime regressions."""

import base64
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def load(name, relative):
    module_spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


spec = load("vmware_spec", "plugins/module_utils/vmware_spec.py")
inspect = load("vmware_inspect", "plugins/modules/vmware_inspect.py")


def guest(**overrides):
    value = dict(
        datacenter="DC",
        folder="/DC/vm/tests",
        cluster="cluster",
        template="linux-template",
        networks=[{"name": "test", "type": "dhcp"}],
        guest={"credentials": {"username": "tester", "password": "dummy-test-value"}},
    )
    return spec.normalize({"instance": value | overrides})["instance"]


def run(value=None):
    value = value or guest()
    return spec.new_run(
        {"instance": value},
        {
            "instance": {
                "folder": "/DC/vm/tests",
                "template": "/DC/vm/template",
                "source_moid": "vm-source",
                "snapshot_moid": "",
            }
        },
        "vc-test",
    )


@pytest.mark.parametrize(
    "override",
    [
        {"typo": True},
        {"cluster": ""},
        {"esxi_hostname": "host"},
        {"connection_port": True},
        {"connection_port": 0},
        {"connection_port": 65536},
        {"instance_os_type": "other"},
        {"connection": "psrp"},
        {"linked_clone": "false"},
        {"linked_clone": True},
        {"hardware": []},
        {"networks": []},
        {"networks": ["bad"]},
        {"networks": [{}]},
        {"networks": [{"name": "test", "ip": "192.0.2.3"}]},
        {"networks": [{"name": "test", "ip": "bad", "netmask": "255.255.255.0"}]},
        {"vcenter": {"validate_certs": "false"}},
        {"vcenter": {"credentials": []}},
        {"vcenter": {"token": "dummy"}},
        {"guest": {"credentials": {"username": "tester"}}},
        {"customization": {"hostname": "-bad"}},
        {"customization": {"password": "different"}},
        {"connection_vars": {"ansible_host": "other"}},
        {"connection_vars": {"ansible_ssh_private_key_file": "other"}},
        {"connection_vars": {"password": "dummy"}},
        {"bootstrap": {"method": "other"}},
        {"bootstrap": {"method": "prepared"}},
        {"name": "folder/name"},
        {"customization": {"unexpected": True}},
    ],
)
def test_invalid_inputs_fail_before_requests(override):
    with pytest.raises((ValueError, TypeError)):
        guest(**override)


def test_recursive_defaults_and_list_replacement():
    values = spec.normalize(
        {"instance": {"hardware": {"memory_mb": 2048}, "networks": [{"name": "override"}]}},
        defaults=guest(hardware={"num_cpus": 2, "memory_mb": 1024}),
    )
    assert values["instance"]["hardware"] == {"num_cpus": 2, "memory_mb": 2048}
    assert values["instance"]["networks"] == [
        {"name": "override", "connected": True, "start_connected": True}
    ]


def test_mixed_vcenters_and_duplicate_fixed_names_rejected():
    for changes in ({"vcenter": {"hostname": "other"}}, {"name": "fixed"}):
        first = guest(name="fixed")
        with pytest.raises(ValueError):
            spec.normalize({"one": first, "two": first | changes})


def test_snapshot_full_and_linked_clone_requests():
    for overrides, expected in (
        ({}, False),
        ({"snapshot_src": "snapshot"}, True),
        ({"snapshot_src": "snapshot", "linked_clone": False}, False),
    ):
        value = guest(**overrides)
        state = run(value)
        request = spec.request(value, state["hosts"]["instance"])
        assert request["linked_clone"] is expected
        assert request.get("snapshot_src") == overrides.get("snapshot_src")
        assert request["state"] == "poweredon"
        assert request["wait_for_customization"] is True
        assert request["annotation"] == state["hosts"]["instance"]["annotation"]


def test_hardware_network_and_placement_forwarding():
    hardware = {"num_cpus": 2, "memory_mb": 2048, "boot_firmware": "efi", "hotadd_cpu": True}
    networks = [
        {
            "name": "distributed",
            "dvswitch_name": "switch",
            "device_type": "vmxnet3",
            "type": "static",
            "ip": "192.0.2.4",
            "netmask": "255.255.255.0",
            "gateway": "192.0.2.1",
        },
        {"name": "secondary", "connected": False},
    ]
    value = guest(hardware=hardware, networks=networks, resource_pool="pool", datastore="store")
    request = spec.request(value, run(value)["hosts"]["instance"])
    assert request["hardware"] == hardware
    assert request["networks"] == [dict(connected=True, start_connected=True) | n for n in networks]
    assert request["resource_pool"] == "pool" and request["datastore"] == "store"
    assert (
        "password" not in request["customization"]
    )  # Linux password reset is not supported by vendor customization.


def test_windows_defaults_and_private_runtime():
    value = guest(
        instance_os_type="windows",
        connection_port=55986,
        customization={"dns_servers": ["192.0.2.53"]},
    )
    request = spec.request(value, run(value)["hosts"]["instance"])
    assert (
        request["customization"]
        | {"fullname": "molecule", "orgname": "molecule", "autologon": False, "autologoncount": 1}
        == request["customization"]
    )
    assert request["customization"]["password"] == "dummy-test-value"
    observation = {
        "management_ips": ["192.0.2.4"],
        "instance_uuid": "uuid",
        "moid": "vm-1",
        "name": "physical",
    }
    runtime = spec.runtime(value, observation, "")
    assert runtime["ansible_winrm_scheme"] == "https"
    assert runtime["ansible_port"] == 55986
    assert runtime["ansible_winrm_transport"] == "ntlm"
    assert runtime["ansible_password"] == "dummy-test-value"
    assert not run(value)["generated_key"]


def test_runtime_management_nic_and_caller_options():
    observation = {
        "management_ips": ["fe80::1", "127.0.0.1", "169.254.2.2", "192.0.2.4"],
        "instance_uuid": "uuid",
        "moid": "vm-1",
        "name": "physical",
    }
    value = guest(connection_vars={"ansible_ssh_common_args": "-o StrictHostKeyChecking=yes"})
    runtime = spec.runtime(value, observation, "/private/key")
    assert runtime["ansible_host"] == "192.0.2.4"
    assert runtime["ansible_ssh_common_args"] == "-o StrictHostKeyChecking=yes"
    assert runtime["ansible_ssh_private_key_file"] == "/private/key"
    observation["management_ips"] += ["192.0.2.5"]
    with pytest.raises(ValueError):
        spec.runtime(value, observation, "/key")
    assert (
        spec.runtime(value | {"connection_address": "reachable.example"}, observation, "/key")[
            "ansible_host"
        ]
        == "reachable.example"
    )


def test_static_runtime_requires_observed_management_address():
    value = guest(networks=[{"name": "net", "ip": "192.0.2.4", "netmask": "255.255.255.0"}])
    observation = {"management_ips": ["192.0.2.5"]}
    with pytest.raises(ValueError):
        spec.runtime(value, observation, "/key")


def test_ownership_isolated_and_never_persists_credentials():
    value = guest(
        vcenter={
            "hostname": "vc.example",
            "credentials": {"username": "api-user", "password": "api-dummy"},
        }
    )
    first, second = run(value), run(value)
    assert first["hosts"]["instance"]["name"] != second["hosts"]["instance"]["name"]
    encoded = json.dumps(first)
    assert (
        "api-dummy" not in encoded
        and "dummy-test-value" not in encoded
        and "api-user" not in encoded
    )
    assert spec.fingerprint(value) == spec.fingerprint(
        guest(vcenter={"hostname": "vc.example", "credentials": {"password": "rotated"}})
    )
    damaged = copy.deepcopy(first)
    damaged["hosts"]["instance"]["annotation"] = second["hosts"]["instance"]["annotation"]
    with pytest.raises(ValueError):
        spec.validate_run(damaged)
    updated = spec.update_host(first, "instance", {"phase": "ready", "instance_uuid": "uuid"})
    assert first["hosts"]["instance"]["phase"] == "planned"
    assert updated["hosts"]["instance"]["phase"] == "ready"
    assert spec.remove_host(updated, "instance")["hosts"] == {}


def test_linux_key_bootstrap_quotes_data_and_preserves_other_keys():
    command = spec.linux_command("ssh-ed25519 AAAA comment'withquote")
    assert "grep -qxF" in command and ">>" in command and "chmod 600" in command
    with pytest.raises(ValueError):
        spec.linux_command("not a public key")


def test_guestinfo_bootstrap_is_key_only_and_not_sysprep():
    value = guest(
        bootstrap={"method": "guestinfo"}, guest={"credentials": {"username": "molecule"}}
    )
    request = spec.request(value, run(value)["hosts"]["instance"], "ssh-ed25519 AAAA test")
    assert "customization" not in request and not request["wait_for_customization"]
    assert request["networks"] == [] and request["state"] == "present"
    assert not request["wait_for_ip_address"]
    settings = {entry["key"]: entry["value"] for entry in request["advanced_settings"]}
    data = yaml.safe_load(base64.b64decode(settings["guestinfo.userdata"]))
    metadata = json.loads(base64.b64decode(settings["guestinfo.metadata"]))
    assert data["users"][0]["ssh_authorized_keys"] == ["ssh-ed25519 AAAA test"]
    assert data["ssh_pwauth"] is False
    assert metadata["network"]["ethernets"]["primary"]["dhcp4"] is True
    assert metadata["local-hostname"] == "instance"


@pytest.mark.parametrize(
    "overrides",
    [
        {"networks": [{"name": "one"}, {"name": "two"}]},
        {"instance_os_type": "windows"},
        {
            "networks": [
                {"name": "one", "type": "static", "ip": "192.0.2.4", "netmask": "255.255.255.0"}
            ]
        },
    ],
)
def test_guestinfo_mode_rejects_unsupported_topologies(overrides):
    with pytest.raises(ValueError):
        guest(bootstrap={"method": "guestinfo"}, **overrides)


def test_unique_inventory_resolution_rejects_ambiguous_names():
    root = SimpleNamespace(parent=None)
    folder = SimpleNamespace(name="DC", parent=root)
    first = SimpleNamespace(name="same", parent=folder)
    second = SimpleNamespace(name="same", parent=folder)
    with pytest.raises(ValueError):
        inspect.unique([first, second], "same", "source")
    assert inspect.inventory_path(first) == "/DC/same"


def test_observation_refuses_markerless_and_replaced_vms(monkeypatch):
    # This policy test needs no SDK or vCenter: the inventory view is a stub.
    monkeypatch.setattr(inspect, "vim", SimpleNamespace(VirtualMachine=object), raising=False)
    vm = SimpleNamespace(
        name="physical",
        parent=object(),
        config=SimpleNamespace(instanceUuid="uuid", annotation="foreign"),
    )
    monkeypatch.setattr(inspect, "view", lambda *args: [vm])
    content = SimpleNamespace(rootFolder=object())
    record = {
        "name": "physical",
        "instance_uuid": "uuid",
        "annotation": "expected",
        "plan": {"folder": "/DC/vm"},
    }
    with pytest.raises(ValueError, match="ownership"):
        inspect.observe(content, {"instance": record})
    monkeypatch.setattr(inspect, "inventory_path", lambda *args: "/DC/vm")
    record["instance_uuid"] = "missing"
    with pytest.raises(ValueError, match="occupied"):
        inspect.observe(content, {"instance": record})


def test_inspection_releases_inventory_views():
    container = SimpleNamespace(view=["entry"], Destroy=Mock())
    content = SimpleNamespace(
        viewManager=SimpleNamespace(CreateContainerView=Mock(return_value=container))
    )
    assert inspect.view(content, object(), []) == ["entry"]
    container.Destroy.assert_called_once()


def test_discovery_never_sends_guest_or_vcenter_credentials():
    public = spec.discovery_specs({"instance": guest()})
    assert "guest" not in public["instance"] and "vcenter" not in public["instance"]
    assert "dummy-test-value" not in json.dumps(public)


def test_network_connection_defaults_preserve_explicit_disconnect():
    value = guest(networks=[{"name": "network", "connected": False, "start_connected": False}])
    assert value["networks"][0]["connected"] is False
    assert value["networks"][0]["start_connected"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"networks": [{"vlan": 10}]},
        {"networks": [{"name": "network", "connected": False}]},
        {"customization": {"dns_servers": ["192.0.2.53"]}},
    ],
)
def test_guestinfo_rejects_silently_ignored_settings(changes):
    with pytest.raises(ValueError):
        guest(bootstrap={"method": "guestinfo"}, **changes)


def test_worker_ids_cannot_escape_the_private_cache():
    state = run()
    state["hosts"]["instance"]["job_id"] = "../../foreign"
    with pytest.raises(ValueError, match="worker identity"):
        spec.validate_run(state)
