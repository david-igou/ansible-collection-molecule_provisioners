"""Behavioral checks for persistent run identity and isolated resource rendering."""

import json
import subprocess

from pathlib import Path

import pytest
import yaml

RUN = {
    "version": 1,
    "id": "abcdef0123456789",
    "hosts": {
        "instance": {
            "name": "instance-digest-abcdef0123456789",
            "namespace": "molecule",
            "service": True,
            "disk": True,
        },
    },
}


@pytest.mark.parametrize(
    "boot_source",
    [
        {"type": "container_disk", "image": "quay.io/containerdisks/ubuntu:24.04"},
        {"type": "data_volume_url", "url": "https://example.com/disk.img", "size": "10Gi"},
        {
            "type": "data_volume_pvc",
            "source": {"name": "golden", "namespace": "images"},
            "size": "10Gi",
        },
        {
            "type": "data_volume_source_ref",
            "source_ref": {"name": "golden", "namespace": "images"},
            "size": "10Gi",
        },
        {"type": "pvc", "name": "external-claim"},
    ],
)
def test_isolated_names_and_disk_ownership(render_vm, boot_source):
    vm = render_vm({"boot_source": boot_source}, RUN)
    name = RUN["hosts"]["instance"]["name"]
    assert vm["metadata"]["name"] == name
    assert vm["spec"]["template"]["metadata"]["labels"]["kubevirt.io/domain"] == name
    assert vm["metadata"]["labels"]["molecule-provisioners.igou.io/run-id"] == RUN["id"]
    if boot_source["type"].startswith("data_volume_"):
        dv = vm["spec"]["dataVolumeTemplates"][0]
        assert dv["metadata"]["name"] == name + "-boot"
        assert dv["metadata"]["labels"]["molecule-provisioners.igou.io/run-id"] == RUN["id"]
        assert dv["spec"]["storage"]["resources"]["requests"]["storage"] == "10Gi"
        assert vm["spec"]["template"]["spec"]["volumes"][0]["dataVolume"]["name"] == name + "-boot"
    elif boot_source["type"] == "pvc":
        assert (
            vm["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"]
            == "external-claim"
        )


def _state(tmp_path, **overrides):
    inventory = tmp_path / "hosts.yml"
    if not inventory.exists():
        inventory.write_text("---\nmolecule:\n  hosts:\n    instance: {}\n")
    extra = {
        "host_spec": {
            "namespace": "molecule",
            "boot_source": {"type": "container_disk", "image": "test"},
            "ssh_service": {"type": "NodePort"},
        },
        "molecule_ephemeral_directory": str(tmp_path),
        "mp_kubevirt_run_isolation": True,
    }
    extra.update(overrides)
    return subprocess.run(
        [
            "ansible-playbook",
            str(Path(__file__).with_name("run_state_harness.yml")),
            "-i",
            str(inventory),
            "-e",
            json.dumps(extra),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_state_survives_separate_processes_and_runs_are_distinct(tmp_path):
    dirs = [tmp_path / "one", tmp_path / "two"]
    states = []
    for directory in dirs:
        directory.mkdir()
        proc = _state(directory)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        state_path = directory / "kubevirt_run.yml"
        original = state_path.read_bytes()
        assert state_path.stat().st_mode & 0o777 == 0o600
        proc = _state(directory)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert state_path.read_bytes() == original
        states.append(yaml.safe_load(original))
    assert states[0]["id"] != states[1]["id"]
    assert states[0]["hosts"]["instance"]["name"] != states[1]["hosts"]["instance"]["name"]


@pytest.mark.parametrize("change", ["namespace", "service", "disk", "hosts", "disable"])
def test_resume_rejects_changed_identity_and_keeps_state(tmp_path, change):
    assert _state(tmp_path).returncode == 0
    state_path = tmp_path / "kubevirt_run.yml"
    original = state_path.read_bytes()
    spec = {
        "namespace": "other" if change == "namespace" else "molecule",
        "ssh_service": {"type": "None" if change == "service" else "NodePort"},
        "boot_source": {"type": "data_volume_url" if change == "disk" else "container_disk"},
    }
    if change == "hosts":
        (tmp_path / "hosts.yml").write_text("---\nmolecule:\n  hosts:\n    replacement: {}\n")
    proc = _state(tmp_path, host_spec=spec, mp_kubevirt_run_isolation=(change != "disable"))
    assert proc.returncode != 0
    assert "Destroy" in proc.stdout or "destroy it before switching" in proc.stdout
    assert state_path.read_bytes() == original


def test_corrupt_state_fails_without_replacing_it(tmp_path):
    state_path = tmp_path / "kubevirt_run.yml"
    state_path.write_text("---\nversion: 999\n")
    proc = _state(tmp_path)
    assert proc.returncode != 0
    assert state_path.read_text() == "---\nversion: 999\n"


def test_fixed_names_do_not_create_run_state(tmp_path):
    proc = _state(tmp_path, mp_kubevirt_run_isolation=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (tmp_path / "kubevirt_run.yml").exists()


def test_long_inventory_names_have_distinct_bounded_resource_names(tmp_path):
    names = ["Long_HOST_" + "a" * 100, "long-host-" + "a" * 100]
    inventory = {"molecule": {"hosts": {name: {} for name in names}}}
    (tmp_path / "hosts.yml").write_text(yaml.safe_dump(inventory, default_flow_style=False))
    spec = {
        "namespace": "molecule",
        "boot_source": {"type": "data_volume_url"},
        "ssh_service": {"type": "NodePort"},
    }
    proc = _state(tmp_path, _mp_specs={name: spec for name in names})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    state = yaml.safe_load((tmp_path / "kubevirt_run.yml").read_text())
    resource_names = [state["hosts"][name]["name"] for name in names]
    assert resource_names[0] != resource_names[1]
    assert all(len(name + "-boot") <= 63 for name in resource_names)
    proc = _state(tmp_path, _mp_specs={name: spec for name in names})
    assert proc.returncode == 0, proc.stdout + proc.stderr
