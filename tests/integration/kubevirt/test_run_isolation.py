"""Opt-in live checks of overlapping YAML consumers and failed-run cleanup.

Run with RUN_KUBEVIRT_ISOLATION=1 and a KubeVirt-enabled KUBECONFIG.
MP_TEST_DATASOURCE optionally exercises generated CDI disks on a real cluster.
"""

import json
import os
import subprocess

from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KUBEVIRT_ISOLATION") != "1",
    reason="requires explicitly enabled KubeVirt integration environment",
)
LABEL = "molecule-provisioners.igou.io/run-id"
FINALIZER = "molecule-provisioners.igou.io/test-cleanup"


class Run:
    """A plain YAML inventory and an independent Molecule ephemeral directory."""

    def __init__(self, directory, namespace, boot_source=None, user="ubuntu"):
        self.directory = directory
        self.ephemeral = directory / "ephemeral"
        (self.ephemeral / "inventory").mkdir(parents=True)
        self.inventory = directory / "hosts.yml"
        inventory = {
            "all": {
                "children": {
                    "molecule": {
                        "vars": {
                            "mp_backend": "kubevirt",
                            "mp_kubevirt_wait_timeout": 600,
                            "consumer_marker": "kept-from-yaml",
                            "mp_defaults": {"kubevirt": {"namespace": namespace}},
                        },
                        "hosts": {
                            "instance": {
                                "mp": {
                                    "kubevirt": {
                                        "boot_source": boot_source
                                        or {
                                            "type": "container_disk",
                                            "image": "quay.io/containerdisks/ubuntu:24.04",
                                        },
                                        "ssh_user": user,
                                        "memory": "1Gi",
                                        "cpu": {"cores": 1},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
        if os.environ.get("MP_TEST_CONNECTION_IP"):
            inventory["all"]["children"]["molecule"]["hosts"]["instance"]["mp"]["kubevirt"][
                "connection_ip"
            ] = os.environ["MP_TEST_CONNECTION_IP"]
        self.inventory.write_text(yaml.safe_dump(inventory, default_flow_style=False))
        self.state_path = self.ephemeral / "kubevirt_run.yml"

    @property
    def state(self):
        return yaml.safe_load(self.state_path.read_text())

    def play(self, phase, *, success=True, extra=None):
        playbook = self.directory / (phase + ".yml")
        playbook.write_text(
            "---\n- name: Run collection lifecycle\n"
            f"  ansible.builtin.import_playbook: david_igou.molecule_provisioners.{phase}\n",
        )
        variables = {"molecule_ephemeral_directory": str(self.ephemeral)}
        variables.update(extra or {})
        proc = subprocess.run(
            [
                "ansible-playbook",
                str(playbook),
                "-i",
                str(self.inventory),
                "-i",
                str(self.ephemeral / "inventory"),
                "-e",
                json.dumps(variables),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=900,
        )
        if success:
            assert proc.returncode == 0, proc.stdout + proc.stderr
        else:
            assert proc.returncode != 0, proc.stdout
        return proc

    def verify_guest(self, expected_hostname=None):
        """Exercise prepare, converge and verify using the original host/groups."""
        self.play("prepare")
        playbook = self.directory / "verify.yml"
        playbook.write_text(
            "---\n- name: Converge and verify consumer guest\n"
            "  hosts: molecule\n  gather_facts: false\n  tasks:\n"
            "    - name: Converge a guest file\n"
            "      ansible.builtin.copy:\n"
            "        content: '{{ consumer_marker }}'\n"
            "        dest: /tmp/mp-run-isolation-verification\n        mode: '0600'\n"
            "    - name: Read converged result\n"
            "      ansible.builtin.slurp:\n"
            "        src: /tmp/mp-run-isolation-verification\n      register: result\n"
            "    - name: Verify logical inventory and consumer result\n"
            "      ansible.builtin.assert:\n        that:\n"
            "          - inventory_hostname == 'instance'\n"
            "          - consumer_marker == 'kept-from-yaml'\n"
            "          - (result.content | b64decode) == consumer_marker\n",
        )
        if expected_hostname:
            with playbook.open("a") as stream:
                stream.write(
                    "    - name: Read guest hostname\n"
                    "      ansible.builtin.command: hostname\n"
                    "      changed_when: false\n      register: guest_hostname\n"
                    "    - name: Verify caller bootstrap executed\n"
                    "      ansible.builtin.assert:\n        that:\n"
                    f"          - guest_hostname.stdout == '{expected_hostname}'\n",
                )
        proc = subprocess.run(
            [
                "ansible-playbook",
                str(playbook),
                "-i",
                str(self.inventory),
                "-i",
                str(self.ephemeral / "inventory"),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


def _resources():
    from kubernetes import config
    from kubernetes.client import ApiClient
    from kubernetes.dynamic import DynamicClient

    config.load_kube_config(config_file=os.environ.get("KUBECONFIG"))
    client = DynamicClient(ApiClient())
    return client.resources


def _object(resource, run, kind="VirtualMachine", name=None):
    host = run.state["hosts"]["instance"]
    api = "v1" if kind == "Service" else "kubevirt.io/v1"
    return resource.get(api_version=api, kind=kind).get(
        name=name or host["name"],
        namespace=host["namespace"],
    )


def test_overlapping_runs_and_failed_cleanup(tmp_path):
    namespace = os.environ.get("MOLECULE_NAMESPACE", "molecule")
    resources = _resources()
    runs = [Run(tmp_path / name, namespace) for name in ("one", "two")]
    try:
        # Initial isolated destroy must be safe before the state file exists.
        for run in runs:
            run.play("destroy")
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda run: run.play("create"), runs))
        names = [run.state["hosts"]["instance"]["name"] for run in runs]
        assert names[0] != names[1]
        ports = []
        for run in runs:
            assert _object(resources, run).metadata.labels[LABEL] == run.state["id"]
            service = _object(resources, run, "Service")
            assert (
                service.spec.selector["kubevirt.io/domain"]
                == run.state["hosts"]["instance"]["name"]
            )
            ports.append(service.spec.ports[0].nodePort)
            runtime = yaml.safe_load((run.ephemeral / "inventory/molecule_runtime.yml").read_text())
            assert list(runtime["all"]["hosts"]) == ["instance"]
        assert ports[0] != ports[1]
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda run: run.verify_guest(), runs))

        first, second = runs
        saved = first.state_path.read_bytes()
        # A fresh create process resumes the same VM, Service, key and port.
        uid = _object(resources, first).metadata.uid
        # Lost state must not select fixed-name or another run's resources.
        first.state_path.unlink()
        try:
            first.play("destroy")
            assert not first.state_path.exists()
        finally:
            first.state_path.write_bytes(saved)
        assert _object(resources, first).metadata.uid == uid
        first.play("create")
        assert first.state_path.read_bytes() == saved
        assert _object(resources, first).metadata.uid == uid
        assert _object(resources, first, "Service").spec.ports[0].nodePort == ports[0]

        services = resources.get(api_version="v1", kind="Service")
        host = first.state["hosts"]["instance"]
        # Ownership mismatch must stop cleanup before either resource is touched.
        services.patch(
            name=host["name"],
            namespace=namespace,
            body={"metadata": {"labels": {LABEL: "other-run"}}},
            content_type="application/merge-patch+json",
        )
        first.play("destroy", success=False)
        assert first.state_path.read_bytes() == saved
        assert _object(resources, first).metadata.uid == uid
        services.patch(
            name=host["name"],
            namespace=namespace,
            body={"metadata": {"labels": {LABEL: first.state["id"]}, "finalizers": [FINALIZER]}},
            content_type="application/merge-patch+json",
        )
        # VM deletion succeeds, Service finalizer fails; state must survive.
        first.play("destroy", success=False, extra={"mp_kubevirt_cleanup_timeout": 2})
        assert first.state_path.read_bytes() == saved
        assert _object(resources, second).metadata.labels[LABEL] == second.state["id"]
        assert _object(resources, second, "Service").spec.ports[0].nodePort == ports[1]
        second.verify_guest()
        services.patch(
            name=host["name"],
            namespace=namespace,
            body={"metadata": {"finalizers": []}},
            content_type="application/merge-patch+json",
        )
        # Explicit destroy must use the saved namespace and hosts, not today's inventory.
        inventory = yaml.safe_load(first.inventory.read_text())
        group = inventory["all"]["children"]["molecule"]
        group["vars"]["mp_defaults"]["kubevirt"]["namespace"] = "unused-changed-namespace"
        group["vars"]["mp_kubevirt_run_isolation"] = False
        group["hosts"]["renamed"] = group["hosts"].pop("instance")
        first.inventory.write_text(yaml.safe_dump(inventory, default_flow_style=False))
        first.play("destroy")
        assert not first.state_path.exists()
        assert not (first.ephemeral / "inventory/molecule_runtime.yml").exists()
        second.verify_guest()
        second.play("destroy")
        assert not second.state_path.exists()
        # Repeated isolated destroy is safe and cannot select fixed names.
        second.play("destroy")
    finally:
        for run in runs:
            if run.state_path.exists():
                host = run.state["hosts"]["instance"]
                services = resources.get(api_version="v1", kind="Service")
                from kubernetes.dynamic.exceptions import NotFoundError

                try:
                    service = services.get(name=host["name"], namespace=host["namespace"])
                    if service.metadata.labels.get(LABEL) in (run.state["id"], "other-run"):
                        services.patch(
                            name=host["name"],
                            namespace=host["namespace"],
                            body={
                                "metadata": {"labels": {LABEL: run.state["id"]}, "finalizers": []}
                            },
                            content_type="application/merge-patch+json",
                        )
                except NotFoundError:
                    pass
                run.play("destroy")


@pytest.mark.skipif(
    not os.environ.get("MP_TEST_DATASOURCE"), reason="needs a CDI golden DataSource"
)
def test_overlapping_generated_disks(tmp_path):
    resources = _resources()
    namespace = os.environ.get("MOLECULE_NAMESPACE", "molecule")
    boot_source = {
        "type": "data_volume_source_ref",
        "source_ref": {
            "name": os.environ["MP_TEST_DATASOURCE"],
            "namespace": os.environ.get(
                "MP_TEST_DATASOURCE_NAMESPACE", "openshift-virtualization-os-images"
            ),
        },
        "size": "30Gi",
    }
    runs = [
        Run(tmp_path / name, namespace, boot_source, "cloud-user")
        for name in ("disk-one", "disk-two")
    ]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda run: run.play("create"), runs))
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda run: run.verify_guest(), runs))
        for run in runs:
            host = run.state["hosts"]["instance"]
            dv = resources.get(api_version="cdi.kubevirt.io/v1beta1", kind="DataVolume").get(
                name=host["name"] + "-boot",
                namespace=namespace,
            )
            assert dv.metadata.labels[LABEL] == run.state["id"]
            claim = resources.get(api_version="v1", kind="PersistentVolumeClaim").get(
                name=host["name"] + "-boot",
                namespace=namespace,
            )
            assert any(owner["uid"] == dv.metadata.uid for owner in claim.metadata.ownerReferences)
        # The retry also exercises ownership checks on CDI-created PVCs.
        runs[0].play("create")
        second_name = runs[1].state["hosts"]["instance"]["name"]
        runs[0].play("destroy")
        assert _object(resources, runs[1]).metadata.name == second_name
        runs[1].verify_guest()
    finally:
        for run in runs:
            if run.state_path.exists():
                run.play("destroy")


def test_failed_create_can_be_destroyed_in_a_fresh_process(tmp_path):
    from copy import deepcopy

    resources = _resources()
    namespace = os.environ.get("MOLECULE_NAMESPACE", "molecule")
    run = Run(tmp_path / "failed-create", namespace)
    inventory = yaml.safe_load(run.inventory.read_text())
    hosts = inventory["all"]["children"]["molecule"]["hosts"]
    hosts["invalid"] = deepcopy(hosts["instance"])
    hosts["invalid"]["mp"]["kubevirt"]["vm_overrides"] = {
        "metadata": {"name": "reserved-fixed-name"}
    }
    run.inventory.write_text(yaml.safe_dump(inventory, sort_keys=False, default_flow_style=False))
    try:
        proc = run.play("create", success=False)
        assert "Run isolation reserves names" in proc.stdout
        assert run.state_path.exists()
        assert _object(resources, run).metadata.labels[LABEL] == run.state["id"]
        run.play("destroy")
        assert not run.state_path.exists()
    finally:
        if run.state_path.exists():
            run.play("destroy")
