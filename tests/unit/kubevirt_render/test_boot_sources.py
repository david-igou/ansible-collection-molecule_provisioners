"""Renderer tests for the boot_source variants."""

from __future__ import annotations


def test_renders_virtualmachine_kind(render_vm) -> None:
    """The renderer produces a kubevirt.io/v1 VirtualMachine object."""
    vm = render_vm(
        {"boot_source": {"type": "container_disk", "image": "quay.io/example/img:latest"}}
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
        }
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
        }
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
        }
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
        }
    )
    cidisk = next(
        v for v in vm["spec"]["template"]["spec"]["volumes"] if v["name"] == "cloudinitdisk"
    )
    assert "name: ubuntu" in cidisk["cloudInitNoCloud"]["userData"]
    assert "name: cloud-user" not in cidisk["cloudInitNoCloud"]["userData"]
