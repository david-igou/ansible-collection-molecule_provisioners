"""Exercise guest bootstrap and replacement network definitions."""

from __future__ import annotations

import json
import subprocess

from pathlib import Path

import pytest
import yaml


def _spec(**options):
    return {"boot_source": {"type": "container_disk", "image": "quay.io/example/test"}, **options}


def _guest(vm):
    return vm["spec"]["template"]["spec"]


def _cloud_init(vm):
    volumes = [v for v in _guest(vm)["volumes"] if v["name"] == "cloudinitdisk"]
    assert len(volumes) == 1
    return volumes[0]["cloudInitNoCloud"]


def test_bootstrap_preserves_ssh_access(render_vm):
    vm = render_vm(
        _spec(cloud_init={"user_data": {"packages": ["python3"], "runcmd": ["echo ready"]}})
    )
    data = yaml.safe_load(_cloud_init(vm)["userData"])
    assert data["packages"] == ["python3"]
    assert data["runcmd"] == ["echo ready"]
    assert data["users"][0]["name"] == "cloud-user"
    assert data["users"][0]["ssh_authorized_keys"] == ["ssh-ed25519 AAAATESTKEY"]


def test_custom_management_user_is_merged_once(render_vm):
    users = [
        {"name": "cloud-user", "groups": ["wheel"], "ssh_authorized_keys": ["ssh-ed25519 OTHER"]},
        "default",
        {"name": "tester"},
    ]
    vm = render_vm(_spec(cloud_init={"user_data": {"users": users}}))
    data = yaml.safe_load(_cloud_init(vm)["userData"])
    management = [u for u in data["users"] if isinstance(u, dict) and u["name"] == "cloud-user"]
    assert len(management) == 1
    assert management[0]["groups"] == ["wheel"]
    assert set(management[0]["ssh_authorized_keys"]) == {
        "ssh-ed25519 OTHER",
        "ssh-ed25519 AAAATESTKEY",
    }
    assert "default" in data["users"]
    assert {"name": "tester"} in data["users"]


def test_network_data_is_independent_of_ssh_bootstrap(render_vm):
    network = {
        "version": 2,
        "ethernets": {"eth0": {"dhcp4": False, "addresses": ["192.0.2.10/24"]}},
    }
    vm = render_vm(_spec(cloud_init={"network_data": network}))
    payload = _cloud_init(vm)
    assert yaml.safe_load(payload["networkData"]) == network
    assert "ssh-ed25519 AAAATESTKEY" in payload["userData"]


def test_external_cloud_init_secrets_are_references(render_vm):
    vm = render_vm(
        _spec(
            cloud_init={
                "user_data_secret": "bootstrap",
                "network_data_secret": "network",
                "inject_ssh_key": False,
            }
        )
    )
    assert _cloud_init(vm) == {
        "secretRef": {"name": "bootstrap"},
        "networkDataSecretRef": {"name": "network"},
    }


def test_cloud_init_can_be_disabled(render_vm):
    vm = render_vm(_spec(cloud_init={"enabled": False}))
    assert [v["name"] for v in _guest(vm)["volumes"]] == ["containerdisk"]
    assert [d["name"] for d in _guest(vm)["domain"]["devices"]["disks"]] == ["containerdisk"]


def test_key_injection_can_be_disabled(render_vm):
    vm = render_vm(
        _spec(cloud_init={"inject_ssh_key": False, "user_data": {"hostname": "test-guest"}})
    )
    data = yaml.safe_load(_cloud_init(vm)["userData"])
    assert data == {"hostname": "test-guest"}


def test_primary_network_replacement_keeps_extras(render_vm):
    interfaces = [{"name": "test-lan", "bridge": {}, "model": "e1000"}]
    networks = [{"name": "test-lan", "multus": {"networkName": "test-lan"}}]
    vm = render_vm(
        _spec(
            interfaces=interfaces,
            networks=networks,
            extra_interfaces=[{"name": "second", "bridge": {}}],
            extra_networks=[{"name": "second", "multus": {"networkName": "second"}}],
        )
    )
    guest = _guest(vm)
    assert guest["domain"]["devices"]["interfaces"][0] == interfaces[0]
    assert guest["networks"][0] == networks[0]
    assert [n["name"] for n in guest["networks"]] == ["test-lan", "second"]
    assert all("pod" not in n for n in guest["networks"])


def test_empty_networks_disable_automatic_pod_interface(render_vm):
    vm = render_vm(_spec(interfaces=[], networks=[]))
    guest = _guest(vm)
    assert guest["networks"] == []
    assert guest["domain"]["devices"]["interfaces"] == []
    assert guest["domain"]["devices"]["autoattachPodInterface"] is False


@pytest.mark.parametrize(
    "options, message",
    [
        ({"cloud_init": {"user_data": "#cloud-config"}}, "user_data"),
        ({"cloud_init": {"network_data": "bad"}}, "network_data"),
        ({"cloud_init": {"user_data": {"users": "bad"}}}, "users"),
        (
            {
                "cloud_init": {
                    "user_data": {"users": [{"name": "cloud-user", "ssh_authorized_keys": "bad"}]}
                }
            },
            "ssh_authorized_keys",
        ),
        (
            {
                "cloud_init": {
                    "user_data": {},
                    "user_data_secret": "bootstrap",
                    "inject_ssh_key": False,
                }
            },
            "mutually exclusive",
        ),
        ({"cloud_init": {"user_data_secret": "bootstrap"}}, "inject_ssh_key"),
        (
            {"cloud_init": {"network_data": {}, "network_data_secret": "network"}},
            "mutually exclusive",
        ),
        ({"cloud_init": {"user_data_secret": "", "inject_ssh_key": False}}, "non-empty"),
        (
            {
                "cloud_init": {
                    "user_data": {"users": [{"name": "cloud-user"}, {"name": "cloud-user"}]}
                }
            },
            "management user",
        ),
        (
            {
                "interfaces": [{"name": "lan", "bridge": {}}],
                "networks": [{"name": "other", "multus": {"networkName": "lan"}}],
            },
            "match",
        ),
        (
            {
                "interfaces": [{"name": "lan", "bridge": {}}],
                "networks": [{"name": "lan", "multus": {"networkName": "lan"}}],
            },
            "management",
        ),
    ],
)
def test_invalid_customization_fails_before_provisioning(run_validate, options, message):
    proc = run_validate(_spec(**options))
    assert proc.returncode != 0
    assert message in proc.stdout + proc.stderr


def test_multus_only_with_explicit_management_address_is_valid(run_validate):
    proc = run_validate(
        _spec(
            interfaces=[{"name": "lan", "bridge": {}}],
            networks=[{"name": "lan", "multus": {"networkName": "lan"}}],
            ssh_service={"type": "None"},
            connection_ip="192.0.2.10",
        )
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_customization_preserves_isolated_names_and_ownership(render_vm):
    run = {
        "id": "abcdef0123456789",
        "hosts": {
            "instance": {"name": "instance-digest-abcdef0123456789", "namespace": "molecule"}
        },
    }
    vm = render_vm(
        _spec(
            namespace="molecule",
            cloud_init={"user_data": {"hostname": "bootstrap"}},
            interfaces=[{"name": "lan", "bridge": {}}],
            networks=[{"name": "lan", "multus": {"networkName": "lan"}}],
        ),
        run=run,
    )
    assert vm["metadata"]["name"] == run["hosts"]["instance"]["name"]
    assert vm["metadata"]["labels"]["molecule-provisioners.igou.io/run-id"] == run["id"]
    assert (
        vm["spec"]["template"]["metadata"]["labels"]["kubevirt.io/domain"] == vm["metadata"]["name"]
    )
    assert yaml.safe_load(_cloud_init(vm)["userData"])["hostname"] == "bootstrap"


def test_podip_uses_configured_network_not_status_order(tmp_path):
    role = Path(__file__).parents[3] / "roles/kubevirt/tasks/_create_vm_dictionary.yml"
    tasks = yaml.safe_load(role.read_text())
    discovery = next(
        t for t in tasks if t["name"] == "Identify the configured pod network for management"
    )
    output = tmp_path / "inventory.json"
    play = [
        {
            "name": "Exercise actual PodIP inventory tasks",
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "item": "instance",
                "mp_kubevirt_ssh_key_path": "/tmp/test-key",
                "__mp_kubevirt_runtime_hosts": {},
                "_mp_specs": {
                    "instance": _spec(
                        ssh_user="cloud-user",
                        ssh_service={"type": "PodIP"},
                        networks=[{"name": "management", "pod": {}}],
                    )
                },
                "__mp_kubevirt_vmi": {
                    "resources": [
                        {
                            "status": {
                                "interfaces": [
                                    {"name": "lan", "ipAddress": "192.0.2.10"},
                                    {"name": "management", "ipAddress": "10.130.0.42"},
                                ]
                            }
                        }
                    ]
                },
            },
            "tasks": [
                discovery,
                tasks[-1],
                {
                    "name": "Save runtime inventory",
                    "ansible.builtin.copy": {
                        "dest": str(output),
                        "content": "{{ __mp_kubevirt_runtime_hosts | to_json }}",
                        "mode": "0600",
                    },
                },
            ],
        }
    ]
    harness = tmp_path / "inventory.yml"
    harness.write_text(yaml.safe_dump(play))
    proc = subprocess.run(
        ["ansible-playbook", str(harness)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(output.read_text())["instance"]["ansible_host"] == "10.130.0.42"


@pytest.mark.parametrize(
    "host_spec, expected",
    [
        ({"connection": "psrp", "cloud_init": {}}, False),
        ({"connection": "psrp", "cloud_init": {"enabled": True, "inject_ssh_key": True}}, True),
        ({"connection": "ssh", "cloud_init": {"enabled": False}}, True),
    ],
)
def test_actual_key_generation_decision(tmp_path, host_spec, expected):
    role = Path(__file__).parents[3] / "roles/kubevirt/tasks/create.yml"
    task = next(
        t
        for t in yaml.safe_load(role.read_text())
        if t["name"] == "Determine whether any host needs an SSH key"
    )
    output = tmp_path / "decision.json"
    play = [
        {
            "name": "Check key generation",
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {"_mp_specs": {"instance": host_spec}},
            "tasks": [
                task,
                {
                    "name": "Save decision",
                    "ansible.builtin.copy": {
                        "dest": str(output),
                        "content": "{{ __mp_kubevirt_any_ssh | to_json }}",
                        "mode": "0600",
                    },
                },
            ],
        }
    ]
    harness = tmp_path / "decision.yml"
    harness.write_text(yaml.safe_dump(play))
    proc = subprocess.run(
        ["ansible-playbook", str(harness)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(output.read_text()) is expected
