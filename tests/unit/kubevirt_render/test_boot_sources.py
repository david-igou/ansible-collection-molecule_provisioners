"""Renderer tests for the boot_source variants."""

from __future__ import annotations


def test_renders_virtualmachine_kind(render_vm) -> None:
    """The renderer produces a kubevirt.io/v1 VirtualMachine object."""
    vm = render_vm(
        {"boot_source": {"type": "container_disk", "image": "quay.io/example/img:latest"}},
    )
    assert vm["kind"] == "VirtualMachine"
    assert vm["apiVersion"] == "kubevirt.io/v1"
    assert vm["metadata"]["name"] == "instance"
    assert vm["metadata"]["namespace"] == "molecule"


def test_container_disk_volume_and_disk(render_vm) -> None:
    """containerDisk type wires a containerDisk volume and a virtio disk."""
    vm = render_vm(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/example/fedora.img"},
        },
    )
    spec = vm["spec"]["template"]["spec"]

    volumes = {v["name"]: v for v in spec["volumes"]}
    assert "containerdisk" in volumes
    assert volumes["containerdisk"]["containerDisk"]["image"] == "quay.io/example/fedora.img"
    assert "cloudinitdisk" in volumes
    assert "users:" in volumes["cloudinitdisk"]["cloudInitNoCloud"]["userData"]

    assert spec["domain"]["devices"]["disks"][0]["name"] == "containerdisk"
    assert spec["volumes"][0]["name"] == "containerdisk"

    disks = {d["name"]: d for d in spec["domain"]["devices"]["disks"]}
    assert disks["containerdisk"]["disk"]["bus"] == "virtio"
    assert disks["cloudinitdisk"]["disk"]["bus"] == "virtio"


def test_container_disk_default_pod_network(render_vm) -> None:
    """containerDisk renders the default pod/masquerade interface."""
    vm = render_vm(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/x"},
        },
    )
    spec = vm["spec"]["template"]["spec"]

    assert spec["domain"]["devices"]["interfaces"][0]["name"] == "default"
    assert spec["domain"]["devices"]["interfaces"][0]["masquerade"] == {}
    assert spec["networks"][0]["name"] == "default"
    assert spec["networks"][0]["pod"] == {}


def test_container_disk_default_ssh_user_baked_into_cloudinit(render_vm) -> None:
    """The cloudinit user-data names the ssh_user (or its role default)."""
    vm = render_vm(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/x"},
        },
    )
    user_data = vm["spec"]["template"]["spec"]["volumes"]
    cidisk = next(v for v in user_data if v["name"] == "cloudinitdisk")
    assert "name: cloud-user" in cidisk["cloudInitNoCloud"]["userData"]


def test_container_disk_custom_ssh_user(render_vm) -> None:
    """ssh_user from the host spec replaces the default in cloud-init userData."""
    vm = render_vm(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/x"},
            "ssh_user": "ubuntu",
        },
    )
    cidisk = next(
        v for v in vm["spec"]["template"]["spec"]["volumes"] if v["name"] == "cloudinitdisk"
    )
    assert "name: ubuntu" in cidisk["cloudInitNoCloud"]["userData"]
    assert "name: cloud-user" not in cidisk["cloudInitNoCloud"]["userData"]


def test_data_volume_url_renders_template(render_vm) -> None:
    """data_volume_url renders dataVolumeTemplates with http source."""
    vm = render_vm(
        {
            "boot_source": {
                "type": "data_volume_url",
                "url": "https://cloud-images.example/x.img",
                "size": "10Gi",
                "storage_class": "standard",
            },
        },
    )
    templates = vm["spec"]["dataVolumeTemplates"]
    assert len(templates) == 1
    dv = templates[0]
    assert dv["metadata"]["name"] == "instance-boot"
    assert dv["spec"]["source"]["http"]["url"] == "https://cloud-images.example/x.img"
    assert "certConfigMap" not in dv["spec"]["source"]["http"]
    assert dv["spec"]["storage"]["resources"]["requests"]["storage"] == "10Gi"
    assert dv["spec"]["storage"]["storageClassName"] == "standard"


def test_data_volume_url_boot_volume_references_template(render_vm) -> None:
    """The VM's boot volume references the dataVolumeTemplate by name."""
    vm = render_vm(
        {
            "boot_source": {
                "type": "data_volume_url",
                "url": "https://x/img",
                "size": "10Gi",
            },
        },
    )
    volumes = vm["spec"]["template"]["spec"]["volumes"]
    boot = next(v for v in volumes if v["name"] == "containerdisk")
    assert boot["dataVolume"]["name"] == "instance-boot"
    assert "containerDisk" not in boot


def test_data_volume_url_omits_storage_class_when_unset(render_vm) -> None:
    """No storageClassName key when storage_class isn't supplied."""
    vm = render_vm(
        {
            "boot_source": {"type": "data_volume_url", "url": "https://x", "size": "10Gi"},
        },
    )
    dv = vm["spec"]["dataVolumeTemplates"][0]
    assert "storageClassName" not in dv["spec"]["storage"]
    assert dv["spec"]["storage"]["resources"]["requests"]["storage"] == "10Gi"


def test_data_volume_pvc_renders_clone_template(render_vm) -> None:
    """data_volume_pvc renders dataVolumeTemplates with pvc source."""
    vm = render_vm(
        {
            "boot_source": {
                "type": "data_volume_pvc",
                "source": {"name": "golden", "namespace": "images"},
                "size": "20Gi",
            },
        },
    )
    dv = vm["spec"]["dataVolumeTemplates"][0]
    assert dv["spec"]["source"]["pvc"] == {"name": "golden", "namespace": "images"}
    assert dv["spec"]["storage"]["resources"]["requests"]["storage"] == "20Gi"

    boot = next(
        v for v in vm["spec"]["template"]["spec"]["volumes"] if v["name"] == "containerdisk"
    )
    assert boot["dataVolume"]["name"] == "instance-boot"
    assert "containerDisk" not in boot


def test_data_volume_pvc_omits_storage_class_when_unset(render_vm) -> None:
    """No storageClassName key on data_volume_pvc when storage_class isn't supplied."""
    vm = render_vm(
        {
            "boot_source": {
                "type": "data_volume_pvc",
                "source": {"name": "golden", "namespace": "images"},
                "size": "20Gi",
            },
        },
    )
    dv = vm["spec"]["dataVolumeTemplates"][0]
    assert "storageClassName" not in dv["spec"]["storage"]
    assert dv["spec"]["storage"]["resources"]["requests"]["storage"] == "20Gi"


def test_pvc_direct_mount(render_vm) -> None:
    """boot_source=pvc directly mounts a PVC, no dataVolumeTemplates."""
    vm = render_vm(
        {
            "boot_source": {"type": "pvc", "name": "existing-boot-pvc"},
        },
    )
    assert "dataVolumeTemplates" not in vm["spec"]
    boot = next(
        v for v in vm["spec"]["template"]["spec"]["volumes"] if v["name"] == "containerdisk"
    )
    assert boot["persistentVolumeClaim"]["claimName"] == "existing-boot-pvc"
    assert "dataVolume" not in boot
    assert "containerDisk" not in boot
