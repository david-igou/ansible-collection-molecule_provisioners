"""CDI data-disk I/O, retry isolation and cleanup on a disposable test cluster."""

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import yaml

from test_definition import full_definition_run
from test_run_isolation import LABEL, Run, _object, _resources

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KUBEVIRT_STORAGE") != "1",
    reason="requires explicitly enabled KubeVirt/CDI storage integration environment",
)
IO_CHECK = """import json, subprocess
devices = json.loads(subprocess.check_output(['lsblk', '--json', '--bytes', '--nodeps', '--output', 'NAME,SIZE,TYPE']))['blockdevices']
disks = [d for d in devices if d['type'] == 'disk' and int(d['size']) == 1073741824]
assert len(disks) == 2, devices
for disk in disks:
    with open('/dev/' + disk['name'], 'r+b', buffering=0) as stream:
        stream.seek(1048576)
        stream.write(b'molecule-storage-ok')
        stream.seek(1048576)
        assert stream.read(19) == b'molecule-storage-ok'
print('both managed data disks support guest I/O')
"""


def _verify_io(run):
    run.play("prepare")
    result = subprocess.run(
        [
            "ansible",
            "molecule",
            "-i",
            str(run.inventory),
            "-i",
            str(run.ephemeral / "inventory"),
            "-b",
            "-m",
            "ansible.builtin.command",
            "-a",
            json.dumps({"argv": ["python3", "-c", IO_CHECK]}),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "both managed data disks support guest I/O" in result.stdout


@pytest.mark.parametrize("mode", ["simple", "full"])
def test_managed_disks_io_resume_and_external_storage_survives(tmp_path, mode):
    from kubernetes.dynamic.exceptions import NotFoundError

    resources = _resources()
    namespace = os.environ.get("MOLECULE_NAMESPACE", "molecule")
    storage_class = os.environ.get("MP_TEST_STORAGE_CLASS", "standard")
    dv_api = resources.get(api_version="cdi.kubevirt.io/v1beta1", kind="DataVolume")
    pvc_api = resources.get(api_version="v1", kind="PersistentVolumeClaim")
    external = "mp-external-" + uuid4().hex[:12]
    storage = {
        "resources": {"requests": {"storage": "1Gi"}},
        "storageClassName": storage_class,
        "volumeMode": "Filesystem",
        "accessModes": ["ReadWriteOnce"],
    }
    dv_api.create(
        namespace=namespace,
        body={
            "apiVersion": "cdi.kubevirt.io/v1beta1",
            "kind": "DataVolume",
            "metadata": {"name": external},
            "spec": {
                "source": {"blank": {}},
                "storage": {**storage, "resources": {"requests": {"storage": "2Gi"}}},
            },
        },
    )
    factory = Run if mode == "simple" else full_definition_run
    runs = [factory(tmp_path / name, namespace) for name in ("storage-one", "storage-two")]
    try:
        for index, run in enumerate(runs):
            inventory = yaml.safe_load(run.inventory.read_text())
            spec = inventory["all"]["children"]["molecule"]["hosts"]["instance"]["mp"]["kubevirt"]
            if mode == "simple":
                spec["boot_disk"] = {"bus": "sata", "boot_order": 1}
                spec["data_disks"] = [
                    {
                        "name": name,
                        "size": "1Gi",
                        "bus": "scsi",
                        "storage_class": storage_class,
                        "volume_mode": "Filesystem",
                        "access_modes": ["ReadWriteOnce"],
                    }
                    for name in ("data-one", "data-two")
                ]
                if index == 0:
                    spec["extra_disks"] = [{"name": "external", "disk": {"bus": "virtio"}}]
                    spec["extra_volumes"] = [{"name": "external", "dataVolume": {"name": external}}]
            else:
                vm = spec["vm_definition"]
                vm["spec"]["dataVolumeTemplates"] = [
                    {
                        "metadata": {"name": name},
                        "spec": {"source": {"blank": {}}, "storage": storage},
                    }
                    for name in ("data-one", "data-two")
                ]
                guest = vm["spec"]["template"]["spec"]
                guest["domain"]["devices"]["disks"].extend(
                    {"name": name, "disk": {"bus": "scsi"}} for name in ("data-one", "data-two")
                )
                guest["volumes"].extend(
                    {"name": name, "dataVolume": {"name": name}}
                    for name in ("data-one", "data-two")
                )
                if index == 0:
                    guest["domain"]["devices"]["disks"].append(
                        {"name": "external", "disk": {"bus": "virtio"}}
                    )
                    guest["volumes"].append({"name": "external", "dataVolume": {"name": external}})
            run.inventory.write_text(yaml.safe_dump(inventory, default_flow_style=False))
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda run: run.play("create"), runs))
            list(executor.map(_verify_io, runs))
        names = [run.state["hosts"]["instance"]["data_volumes"] for run in runs]
        assert len(names[0]) == len(names[1]) == 2
        assert set(names[0]).isdisjoint(names[1])
        for run, disks in zip(runs, names):
            for name in disks:
                dv = dv_api.get(name=name, namespace=namespace)
                claim = pvc_api.get(name=name, namespace=namespace)
                assert dv.metadata.labels[LABEL] == run.state["id"]
                assert any(
                    owner["uid"] == dv.metadata.uid for owner in claim.metadata.ownerReferences
                )
        uid = _object(resources, runs[0]).metadata.uid
        runs[0].play("create")
        assert _object(resources, runs[0]).metadata.uid == uid
        runs[0].play("destroy")
        for name in names[0]:
            with pytest.raises(NotFoundError):
                pvc_api.get(name=name, namespace=namespace)
        assert dv_api.get(name=external, namespace=namespace)
        assert pvc_api.get(name=external, namespace=namespace)
        _verify_io(runs[1])
        runs[1].play("destroy")
        runs[1].play("destroy")
        for name in names[1]:
            with pytest.raises(NotFoundError):
                pvc_api.get(name=name, namespace=namespace)
    finally:
        for run in runs:
            if run.state_path.exists():
                run.play("destroy")
        dv_api.delete(name=external, namespace=namespace, body={"propagationPolicy": "Foreground"})
        for _attempt in range(60):
            try:
                pvc_api.get(name=external, namespace=namespace)
            except NotFoundError:
                break
            time.sleep(2)
        else:
            pytest.fail("external test fixture PVC was not cleaned up")
