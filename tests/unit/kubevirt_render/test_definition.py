"""Full manifests replace generated configuration but retain managed identity."""

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest
import yaml


def definition():
    return {
        "apiVersion": "kubevirt.io/v1",
        "kind": "VirtualMachine",
        "metadata": {"name": "original", "labels": {"purpose": "testing"}},
        "spec": {
            "runStrategy": "RerunOnFailure",
            "template": {
                "metadata": {"annotations": {"example.com/test": "kept"}},
                "spec": {
                    "domain": {
                        "resources": {"requests": {"memory": "512Mi"}},
                        "devices": {
                            "rng": {},
                            "disks": [{"name": "boot", "disk": {"bus": "sata"}}],
                        },
                    },
                    "networks": [{"name": "management", "pod": {}}],
                    "volumes": [{"name": "boot", "containerDisk": {"image": "test-image"}}],
                    "terminationGracePeriodSeconds": 15,
                },
            },
        },
    }


def isolated_run():
    return {
        "id": "abcdef0123456789",
        "hosts": {
            "instance": {"name": "instance-digest-abcdef0123456789", "namespace": "molecule"}
        },
    }


def with_disks():
    vm = definition()
    vm["spec"]["dataVolumeTemplates"] = [
        {
            "metadata": {"name": name, "labels": {"disk-role": name}},
            "spec": {"source": {"blank": {}}},
        }
        for name in ("boot-image", "scratch")
    ]
    vm["spec"]["template"]["spec"]["volumes"] = [
        {"name": "boot", "dataVolume": {"name": "boot-image"}},
        {"name": "scratch", "dataVolume": {"name": "scratch", "hotpluggable": True}},
        {"name": "external", "persistentVolumeClaim": {"claimName": "caller-owned"}},
        {"name": "external-dv", "dataVolume": {"name": "caller-owned-dv"}},
    ]
    return vm


def test_full_definition_does_not_inject_vm_defaults(render_vm):
    original = definition()
    vm = render_vm({"vm_definition": original})
    expected = deepcopy(original)
    expected["metadata"].update(name="instance", namespace="molecule")
    expected["metadata"]["labels"]["kubevirt.io/domain"] = "instance"
    expected["spec"]["template"]["metadata"]["labels"] = {"kubevirt.io/domain": "instance"}
    assert vm == expected
    assert "running" not in vm["spec"]
    assert "cpu" not in vm["spec"]["template"]["spec"]["domain"]


def test_full_definition_bootstrap_is_unchanged(render_vm):
    original = definition()
    bootstrap = {
        "name": "seed",
        "cloudInitNoCloud": {"userData": "#cloud-config\nhostname: custom\n"},
    }
    original["spec"]["template"]["spec"]["volumes"].append(bootstrap)
    vm = render_vm({"vm_definition": original})
    assert (
        vm["spec"]["template"]["spec"]["volumes"] == original["spec"]["template"]["spec"]["volumes"]
    )


def test_isolated_definition_rewrites_only_owned_disk_references(render_vm):
    run = isolated_run()
    vm = render_vm({"vm_definition": with_disks()}, run)
    disks = vm["spec"]["dataVolumeTemplates"]
    names = [disk["metadata"]["name"] for disk in disks]
    assert len(set(names)) == 2
    assert all(len(name) <= 63 for name in names)
    assert all(
        disk["metadata"]["labels"]["molecule-provisioners.igou.io/run-id"] == run["id"]
        for disk in disks
    )
    assert disks[0]["metadata"]["labels"]["disk-role"] == "boot-image"
    volumes = vm["spec"]["template"]["spec"]["volumes"]
    assert volumes[0]["dataVolume"]["name"] == names[0]
    assert volumes[1]["dataVolume"] == {"name": names[1], "hotpluggable": True}
    assert volumes[2]["persistentVolumeClaim"]["claimName"] == "caller-owned"
    assert volumes[3]["dataVolume"]["name"] == "caller-owned-dv"
    assert vm["metadata"]["name"] == run["hosts"]["instance"]["name"]


def test_fixed_definition_preserves_embedded_disk_names(render_vm):
    vm = render_vm({"vm_definition": with_disks()})
    assert [disk["metadata"]["name"] for disk in vm["spec"]["dataVolumeTemplates"]] == [
        "boot-image",
        "scratch",
    ]


def test_full_definition_is_valid_without_boot_source(run_validate):
    proc = run_validate({"vm_definition": definition()})
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "parameter",
    [
        "boot_source",
        "cpu",
        "memory",
        "cloud_init",
        "interfaces",
        "extra_volumes",
        "vm_overrides",
        "sysprep_secret",
    ],
)
def test_definition_rejects_simple_parameters(run_validate, parameter):
    proc = run_validate({"vm_definition": definition(), parameter: {}})
    assert proc.returncode != 0
    assert "mutually exclusive" in proc.stdout


@pytest.mark.parametrize("vm", [None, [], {}, {"apiVersion": "v1", "kind": "Pod", "spec": {}}])
def test_definition_rejects_non_vm_inputs(run_validate, vm):
    proc = run_validate({"vm_definition": vm})
    assert proc.returncode != 0


@pytest.mark.parametrize("change", ["namespace", "status", "duplicate-disks", "no-management"])
def test_definition_rejects_invalid_managed_configuration(run_validate, change):
    vm = with_disks()
    if change == "namespace":
        vm["metadata"]["namespace"] = "different"
    elif change == "status":
        vm["status"] = {}
    elif change == "duplicate-disks":
        vm["spec"]["dataVolumeTemplates"][1]["metadata"]["name"] = "boot-image"
    else:
        guest = vm["spec"]["template"]["spec"]
        guest["networks"] = []
        guest["domain"]["devices"]["autoattachPodInterface"] = False
    proc = run_validate({"vm_definition": vm})
    assert proc.returncode != 0


def test_definition_supports_multus_only_explicit_access(run_validate):
    vm = definition()
    vm["spec"]["template"]["spec"]["networks"] = [
        {"name": "test", "multus": {"networkName": "test"}}
    ]
    proc = run_validate(
        {"vm_definition": vm, "ssh_service": {"type": "None"}, "connection_ip": "192.0.2.10"}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("shared_memory", [False, True])
def test_actual_spec_merge_only_applies_connection_defaults(tmp_path, shared_memory):
    root = Path(__file__).parents[3]
    inventory = tmp_path / "hosts.yml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "molecule": {
                    "hosts": {"instance": {"mp": {"kubevirt": {"vm_definition": definition()}}}}
                }
            },
            default_flow_style=False,
        )
    )
    output = tmp_path / "merged.yml"
    shared = {"namespace": "custom", "ssh_user": "tester"}
    if shared_memory:
        shared["memory"] = "2Gi"
    play = [
        {
            "name": "Exercise production spec merge",
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {"mp_defaults": {"kubevirt": shared}},
            "tasks": [
                {
                    "name": "Load defaults",
                    "ansible.builtin.include_vars": str(root / "roles/kubevirt/defaults/main.yml"),
                },
                {
                    "name": "Merge input",
                    "ansible.builtin.include_tasks": str(
                        root / "roles/kubevirt/tasks/_spec_merge.yml"
                    ),
                },
                {
                    "name": "Save resolved spec",
                    "ansible.builtin.copy": {
                        "dest": str(output),
                        "content": "{{ _mp_specs.instance | to_nice_yaml }}",
                        "mode": "0600",
                    },
                },
            ],
        }
    ]
    harness = tmp_path / "merge.yml"
    harness.write_text(yaml.safe_dump(play, default_flow_style=False))
    proc = subprocess.run(
        ["ansible-playbook", str(harness), "-i", str(inventory)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    merged = yaml.safe_load(output.read_text())
    assert merged["namespace"] == "custom"
    assert merged["ssh_user"] == "tester"
    assert "interfaces" not in merged
    assert "cloud_init" not in merged
    assert ("memory" in merged) is shared_memory


@pytest.mark.parametrize("existing_key", [False, True])
def test_full_definition_requires_a_caller_key(tmp_path, existing_key):
    root = Path(__file__).parents[3]
    tasks = yaml.safe_load((root / "roles/kubevirt/tasks/create.yml").read_text())
    key = tmp_path / "identity"
    if existing_key:
        key.write_text("test-only-placeholder")
    play = [
        {
            "name": "Exercise full-definition key prerequisite",
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "_mp_specs": {"instance": {"connection": "ssh", "vm_definition": definition()}},
                "mp_kubevirt_ssh_key_path": str(key),
            },
            "tasks": [
                task
                for task in tasks
                if task["name"]
                in (
                    "Check the caller's SSH key for full definitions",
                    "Require an existing SSH key for full definitions",
                )
            ],
        }
    ]
    harness = tmp_path / "key.yml"
    harness.write_text(yaml.safe_dump(play, default_flow_style=False))
    proc = subprocess.run(
        ["ansible-playbook", str(harness)], capture_output=True, text=True, check=False
    )
    assert (proc.returncode == 0) is existing_key, proc.stdout + proc.stderr
    if not existing_key:
        assert "does not inject SSH access" in proc.stdout
