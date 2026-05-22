"""Renderer tests for the boot_source variants."""

from __future__ import annotations


def test_renders_virtualmachine_kind(render_vm) -> None:
    """The renderer produces a kubevirt.io/v1 VirtualMachine object."""
    vm = render_vm({"boot_source": {"type": "container_disk", "image": "quay.io/example/img:latest"}})
    assert vm["kind"] == "VirtualMachine"
    assert vm["apiVersion"] == "kubevirt.io/v1"
    assert vm["metadata"]["name"] == "instance"
    assert vm["metadata"]["namespace"] == "molecule"
