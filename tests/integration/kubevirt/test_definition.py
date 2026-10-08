"""Opt-in full-manifest lifecycle checks on the CI KubeVirt cluster."""

import os
import subprocess

import pytest
import yaml

from test_run_isolation import Run, _object, _resources

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KUBEVIRT_ISOLATION") != "1",
    reason="requires explicitly enabled KubeVirt integration environment",
)


def full_definition_run(directory, namespace):
    run = Run(directory, namespace)
    key = run.ephemeral / "identity_file"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    public_key = key.with_suffix(".pub").read_text().strip()
    cloud_config = {
        "hostname": "declarative-guest",
        "users": [
            {
                "name": "ubuntu",
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "ssh_authorized_keys": [public_key],
            }
        ],
    }
    vm = {
        "apiVersion": "kubevirt.io/v1",
        "kind": "VirtualMachine",
        "metadata": {"name": "same-original-name", "labels": {"mode": "full-definition"}},
        "spec": {
            "runStrategy": "RerunOnFailure",
            "template": {
                "spec": {
                    "domain": {
                        "cpu": {"cores": 1},
                        "resources": {"requests": {"memory": "1Gi"}},
                        "devices": {
                            "rng": {},
                            "interfaces": [{"name": "management", "masquerade": {}}],
                            "disks": [
                                {"name": "root", "disk": {"bus": "virtio"}},
                                {"name": "seed", "disk": {"bus": "virtio"}},
                            ],
                        },
                    },
                    "networks": [{"name": "management", "pod": {}}],
                    "volumes": [
                        {
                            "name": "root",
                            "containerDisk": {"image": "quay.io/containerdisks/ubuntu:24.04"},
                        },
                        {
                            "name": "seed",
                            "cloudInitNoCloud": {
                                "userData": "#cloud-config\n" + yaml.safe_dump(cloud_config)
                            },
                        },
                    ],
                },
            },
        },
    }
    inventory = yaml.safe_load(run.inventory.read_text())
    spec = {"ssh_user": "ubuntu", "vm_definition": vm}
    if os.environ.get("MP_TEST_CONNECTION_IP"):
        spec["connection_ip"] = os.environ["MP_TEST_CONNECTION_IP"]
    inventory["all"]["children"]["molecule"]["hosts"]["instance"]["mp"]["kubevirt"] = spec
    run.inventory.write_text(yaml.safe_dump(inventory, default_flow_style=False))
    return run


def test_full_definitions_boot_resume_and_cleanup_independently(tmp_path):
    namespace = os.environ.get("MOLECULE_NAMESPACE", "molecule")
    resources = _resources()
    runs = [full_definition_run(tmp_path / name, namespace) for name in ("full-one", "full-two")]
    try:
        for run in runs:
            original_key = (run.ephemeral / "identity_file").read_bytes()
            run.play("create")
            vm = _object(resources, run)
            assert vm.spec.runStrategy == "RerunOnFailure"
            assert vm.spec.get("running") is None
            assert [v.name for v in vm.spec.template.spec.volumes] == ["root", "seed"]
            assert vm.metadata.labels["mode"] == "full-definition"
            run.verify_guest(expected_hostname="declarative-guest")
            uid = vm.metadata.uid
            run.play("create")
            assert _object(resources, run).metadata.uid == uid
            assert (run.ephemeral / "identity_file").read_bytes() == original_key
        assert (
            runs[0].state["hosts"]["instance"]["name"] != runs[1].state["hosts"]["instance"]["name"]
        )
        runs[0].play("destroy")
        assert not runs[0].state_path.exists()
        runs[1].verify_guest(expected_hostname="declarative-guest")
        runs[1].play("destroy")
        runs[1].play("destroy")
    finally:
        for run in runs:
            if run.state_path.exists():
                run.play("destroy")
