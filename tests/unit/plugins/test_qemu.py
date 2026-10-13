"""QEMU validation, ownership, reservation and bootstrap regressions."""

import importlib.util
import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name):
    module_spec = importlib.util.spec_from_file_location(
        name, ROOT / "plugins" / "module_utils" / (name + ".py")
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


spec = load("qemu_spec")
runtime = load("qemu_runtime")


def hosts(**changes):
    return spec.normalize(
        {
            "guest": {
                "image": "file:///image.qcow2",
                "memory": 512,
                "cpus": 1,
                "firmware": "bios",
                "ssh_user": "molecule",
                **changes,
            }
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"cpus": 0},
        {"memory": True},
        {"firmware": "typo"},
        {"ssh_port": 0},
        {"ssh_host_port": "2222"},
        {"disk_size": "+2G"},
        {"connection": "winrm"},
        {"connection_vars": {"ansible_port": 22}},
        {"cloud_init": {"enabled": False, "inject_ssh_key": True}},
        {"cloud_init": {"metadata": {"instance-id": "unowned"}}},
        {"data_disks": [{"name": "../disk", "size": "1G"}]},
        {"data_disks": [{"name": "disk"}]},
        {"data_disks": [{"name": "disk", "size": "1G", "bus": "usb"}]},
        {"extra_args": "-S"},
        {"extra_args": ["-uuid", "unowned"]},
        {"extra_args": ["-netdev", "user,id=net0"]},
        {"application_ports": [{"name": "dns", "port": 53, "protocol": "SCTP"}]},
        {"nics": [{"name": "lan", "network": "missing"}]},
        {"networks": [{"name": "lan", "type": "socket"}]},
        {
            "connection": "network_cli",
            "connection_vars": {"ansible_network_os": "community.routeros.routeros"},
        },
    ],
)
def test_reject_invalid_merged_inputs(changes):
    with pytest.raises(ValueError):
        hosts(**changes)


def test_bootstrap_preserves_users_permissions_and_keys():
    guest = hosts(
        cloud_init={
            "user_data": {
                "users": [
                    {"name": "other"},
                    {
                        "name": "molecule",
                        "sudo": False,
                        "ssh_authorized_keys": ["caller-key"],
                    },
                ]
            }
        }
    )["guest"]
    data = spec.user_data(guest, "generated-key")
    assert data["users"][0] == {"name": "other"}
    assert data["users"][1]["sudo"] is False
    assert data["users"][1]["ssh_authorized_keys"] == ["caller-key", "generated-key"]
    assert spec.user_data(guest, "generated-key") == data


def test_replace_and_disabled_bootstrap():
    guest = hosts(
        cloud_init={
            "mode": "replace",
            "inject_ssh_key": False,
            "user_data": {"packages": ["curl"]},
        }
    )["guest"]
    assert spec.user_data(guest, "") == {"packages": ["curl"]}
    guest = hosts(
        connection="network_cli",
        connection_probe="/system identity print",
        connection_vars={"ansible_network_os": "community.routeros.routeros"},
    )["guest"]
    assert not guest["cloud_init"]["enabled"]
    assert not guest["cloud_init"]["inject_ssh_key"]


def test_manifest_requests_exclude_credentials_and_user_data():
    original = hosts(
        connection_vars={"ansible_password": "private-credential"},
        cloud_init={"user_data": {"write_files": [{"content": "private-bootstrap"}]}},
    )
    requests = spec.requests(original)
    serialized = json.dumps(requests)
    assert "private-credential" not in serialized
    assert "private-bootstrap" not in serialized
    changed = hosts(
        connection_vars={"ansible_password": "different"},
        cloud_init={"user_data": {"write_files": [{"content": "private-bootstrap"}]}},
    )
    assert spec.requests(changed) == requests


def test_parallel_reservations_reuse_and_explicit_conflicts(tmp_path):
    first = runtime.Runtime(tmp_path / "one", tmp_path / "shared")
    second = runtime.Runtime(tmp_path / "two", tmp_path / "shared")
    requests = spec.requests(
        hosts(application_ports=[{"name": "dns", "port": 53, "protocol": "UDP"}])
    )
    with first.locked():
        assert first.reserve(requests)
        original = first.state["hosts"]["guest"]["ssh_port"]
        assert not first.reserve(requests)
    with second.locked():
        assert second.reserve(requests)
        assert second.state["hosts"]["guest"]["ssh_port"] != original
    third = runtime.Runtime(tmp_path / "conflict", tmp_path / "shared")
    with third.locked(), pytest.raises(ValueError, match="reserved"):
        third.reserve(spec.requests(hosts(ssh_host_port=original)))
    with first.locked():
        assert first.destroy()
    with second.locked():
        assert second.destroy()
    assert json.loads((tmp_path / "shared" / "qemu-reservations.json").read_text()) == {}


def test_occupied_explicit_port_and_configuration_change(tmp_path):
    run = runtime.Runtime(tmp_path / "run", tmp_path / "shared")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        with run.locked(), pytest.raises(ValueError, match="occupied"):
            run.reserve(spec.requests(hosts(ssh_host_port=listener.getsockname()[1])))
    with run.locked():
        run.state = {}
        run.reservations = {}
        run.reserve(spec.requests(hosts()))
        with pytest.raises(ValueError, match="configuration changed"):
            run.reserve(spec.requests(hosts(memory=1024)))
        run.destroy()


def test_unrelated_qemu_process_is_never_signaled():
    record = {
        "pid": 123,
        "start_time": "old",
        "executable": "/usr/bin/qemu-system-x86_64",
        "qmp": "/missing",
        "vm_uuid": "original",
    }
    with (
        patch.object(runtime, "process", return_value={**record, "start_time": "new"}),
        patch.object(runtime.signal, "pidfd_send_signal") as kill,
    ):
        with pytest.raises(ValueError, match="identity changed"):
            runtime.owned(record)
        kill.assert_not_called()


def test_mismatched_uuid_and_unavailable_qmp_are_not_ownership():
    record = {
        "pid": 123,
        "start_time": "one",
        "executable": "/qemu",
        "qmp": "/socket",
        "vm_uuid": "owned",
    }
    with (
        patch.object(runtime, "process", return_value=record),
        patch.object(runtime, "qmp", return_value={"UUID": "other"}),
        pytest.raises(ValueError, match="UUID"),
    ):
        runtime.owned(record)
    with (
        patch.object(runtime, "process", return_value=record),
        patch.object(runtime, "qmp", side_effect=OSError("unreachable")),
        pytest.raises(OSError),
    ):
        runtime.owned(record)


def test_legacy_pidfile_is_not_adopted(tmp_path):
    (tmp_path / "guest.pid").write_text("123")
    run = runtime.Runtime(tmp_path, tmp_path / "shared")
    with run.locked(), pytest.raises(ValueError, match="Legacy"):
        run.reserve(spec.requests(hosts()))


def test_socket_link_members_and_disks_translate(tmp_path):
    original = {
        "a": {
            "image": "file:///image",
            "memory": 512,
            "cpus": 1,
            "firmware": "bios",
            "ssh_user": "test",
            "networks": [{"name": "lan", "type": "socket"}],
            "nics": [{"name": "extra", "network": "lan"}],
            "data_disks": [{"name": "data", "size": "1G", "bus": "scsi", "serial": "test-disk"}],
        },
        "b": {
            "image": "file:///image",
            "memory": 512,
            "cpus": 1,
            "firmware": "bios",
            "ssh_user": "test",
            "networks": [{"name": "lan", "type": "socket"}],
            "nics": [{"name": "extra", "network": "lan"}],
        },
    }
    normalized = spec.normalize(original)
    run = runtime.Runtime(tmp_path / "run", tmp_path / "shared")
    with run.locked():
        run.reserve(spec.requests(normalized))
        first = spec.launch_argv(normalized["a"], run.state["hosts"]["a"], False, "")
        second = spec.launch_argv(normalized["b"], run.state["hosts"]["b"], False, "")
        assert any("listen=127.0.0.1:" in arg for arg in first)
        assert any("connect=127.0.0.1:" in arg for arg in second)
        assert any("scsi-hd,bus=mp-scsi.0" in arg for arg in first)
        assert "-uuid" in first and "-name" in first
        run.destroy()


def test_network_customization_preserves_management_and_allows_override():
    instance = hosts(
        cloud_init={
            "network_data": {
                "version": 2,
                "ethernets": {"test0": {"addresses": ["192.0.2.1/24"]}},
            }
        }
    )["guest"]
    identity = {"vm_uuid": "unit-test-uuid"}
    data = spec.network_data(instance, identity)
    assert data["ethernets"]["test0"]["addresses"] == ["192.0.2.1/24"]
    assert data["ethernets"]["mp-mgmt"]["dhcp4"] is True
    assert data["ethernets"]["mp-mgmt"]["match"]["macaddress"] == spec.stable_mac(
        identity, "management"
    )
    instance["cloud_init"]["network_data"]["ethernets"]["mp-mgmt"] = {
        "dhcp4": False,
        "addresses": ["10.0.2.15/24"],
    }
    assert not spec.network_data(instance, identity)["ethernets"]["mp-mgmt"]["dhcp4"]


def test_auto_mac_is_stable_and_distinct_between_vm_identities():
    first = {"vm_uuid": "first"}
    second = {"vm_uuid": "second"}
    assert spec.stable_mac(first, "nic") == spec.stable_mac(first, "nic")
    assert spec.stable_mac(first, "nic") != spec.stable_mac(second, "nic")
    assert spec.stable_mac(first, "nic") != spec.stable_mac(first, "management")


def test_dead_interrupted_launch_can_be_cleaned(tmp_path):
    run = runtime.Runtime(tmp_path / "run", tmp_path / "cache")
    with run.locked():
        run.reserve(spec.requests(hosts()))
        record = run.state["hosts"]["guest"]
        record["status"] = "launching"
        record["executable"] = "/qemu"
        Path(record["pidfile"]).write_text("99999999")
        run.save()
        with patch.object(runtime, "process", return_value=None):
            assert run.destroy()
    assert not run.path.exists()


def test_unverified_interrupted_launch_retains_artifacts(tmp_path):
    run = runtime.Runtime(tmp_path / "run", tmp_path / "cache")
    with run.locked():
        run.reserve(spec.requests(hosts()))
        record = run.state["hosts"]["guest"]
        record["status"] = "launching"
        Path(record["qmp"]).touch()
        Path(record["overlay"]).touch()
        run.save()
        with pytest.raises(ValueError, match="Unverified"):
            run.destroy()
    assert run.path.exists()
    assert Path(record["overlay"]).exists()


def test_reservations_with_same_path_belong_to_distinct_runs(tmp_path):
    run = runtime.Runtime(tmp_path / "run", tmp_path / "cache")
    with run.locked():
        run.reserve(spec.requests(hosts()))
        foreign_key = "TCP:127.0.0.1:1"
        run.reservations[foreign_key] = {
            "manifest": str(run.path),
            "run_uuid": "other-controller",
        }
        run.save()
        run.destroy()
        assert foreign_key in run.reservations


def test_serial_capture_is_boot_configuration_but_retention_is_mutable(tmp_path):
    enabled = hosts()
    disabled = hosts(diagnostics={"serial_log": False})
    assert (
        spec.requests(enabled)["guest"]["fingerprint"]
        != spec.requests(disabled)["guest"]["fingerprint"]
    )
    run = runtime.Runtime(tmp_path / "run", tmp_path / "cache")
    with run.locked():
        run.reserve(spec.requests(enabled))
        assert run.reserve(spec.requests(hosts(diagnostics={"retain_logs": False})))
        assert not run.state["hosts"]["guest"]["diagnostics"]["retain_logs"]
        run.destroy()


def test_legacy_port_binding_is_not_replaced_automatically(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        selected = probe.getsockname()[1]
    run = runtime.Runtime(tmp_path / "run", tmp_path / "cache")
    with run.locked():
        run.reserve(spec.requests(hosts()), isolation=False, base=selected)
        record = run.state["hosts"]["guest"]
        assert not record["auto_ssh_port"]
        with socket.socket() as outsider:
            outsider.bind(("127.0.0.1", selected))
            with pytest.raises(ValueError, match="Explicit"):
                run.retry_ports(record, [])
        assert record["ssh_port"] == selected
        run.destroy()


def test_persisted_launch_order_and_port_policy_survive_inventory_reordering(tmp_path):
    original = spec.normalize({"z": hosts()["guest"], "a": hosts()["guest"]})
    run = runtime.Runtime(tmp_path / "run", tmp_path / "cache")
    with run.locked():
        run.reserve(spec.requests(original))
    resumed = runtime.Runtime(tmp_path / "run", tmp_path / "cache")
    with resumed.locked():
        assert list(resumed.state["hosts"]) == ["a", "z"]
        assert resumed.state["launch_order"] == ["z", "a"]
        reordered = {"a": original["a"], "z": original["z"]}
        assert not resumed.reserve(spec.requests(reordered))
        assert resumed.state["launch_order"] == ["z", "a"]
        with pytest.raises(ValueError, match="port policy"):
            resumed.reserve(spec.requests(original), isolation=False)
        resumed.destroy()


def test_bootstrap_key_and_uefi_paths_are_immutable_boot_inputs():
    source = hosts(firmware="uefi")
    original = spec.requests(
        source, {"ssh_key_path": "/one", "ovmf_code": "/code", "ovmf_vars": "/vars"}
    )
    changed_key = spec.requests(
        source, {"ssh_key_path": "/two", "ovmf_code": "/code", "ovmf_vars": "/vars"}
    )
    changed_firmware = spec.requests(
        source, {"ssh_key_path": "/one", "ovmf_code": "/new-code", "ovmf_vars": "/vars"}
    )
    assert original["guest"]["fingerprint"] != changed_key["guest"]["fingerprint"]
    assert original["guest"]["fingerprint"] != changed_firmware["guest"]["fingerprint"]


@pytest.mark.parametrize(
    "option", ["--uuid", "--name", "--qmp", "--pidfile", "--readconfig", "--daemonize"]
)
def test_double_dash_identity_overrides_are_rejected(option):
    with pytest.raises(ValueError, match="managed process identity"):
        hosts(extra_args=[option, "replacement"])


def test_application_binding_accepts_a_specific_ipv4_interface():
    source = hosts(application_ports=[{"name": "api", "port": 8080, "bind_address": "127.0.0.2"}])
    assert source["guest"]["application_ports"][0]["bind_address"] == "127.0.0.2"
    with pytest.raises(ValueError, match="IPv4"):
        hosts(application_ports=[{"name": "api", "port": 8080, "bind_address": "not-an-address"}])
