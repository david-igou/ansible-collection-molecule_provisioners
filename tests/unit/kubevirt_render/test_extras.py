"""Renderer tests for extra_disks/volumes/interfaces/networks (list-append)."""

from __future__ import annotations


def _base(extra: dict | None = None) -> dict:
    spec: dict = {"boot_source": {"type": "container_disk", "image": "quay.io/x"}}
    if extra:
        spec.update(extra)
    return spec


def test_extra_disks_appended_after_defaults(render_vm) -> None:
    """extra_disks come after [containerdisk, cloudinitdisk]."""
    vm = render_vm(_base({"extra_disks": [{"name": "scratch", "disk": {"bus": "virtio"}}]}))
    names = [d["name"] for d in vm["spec"]["template"]["spec"]["domain"]["devices"]["disks"]]
    assert names == ["containerdisk", "cloudinitdisk", "scratch"]


def test_extra_volumes_appended(render_vm) -> None:
    vm = render_vm(
        _base(
            {
                "extra_volumes": [{"name": "scratch", "emptyDisk": {"capacity": "5Gi"}}],
            },
        ),
    )
    names = [v["name"] for v in vm["spec"]["template"]["spec"]["volumes"]]
    assert names == ["containerdisk", "cloudinitdisk", "scratch"]


def test_extra_interfaces_and_networks_appended(render_vm) -> None:
    vm = render_vm(
        _base(
            {
                "extra_interfaces": [{"name": "bridge0", "bridge": {}}],
                "extra_networks": [{"name": "bridge0", "multus": {"networkName": "my-net"}}],
            },
        ),
    )
    spec = vm["spec"]["template"]["spec"]
    ifaces = [i["name"] for i in spec["domain"]["devices"]["interfaces"]]
    nets = [n["name"] for n in spec["networks"]]
    assert ifaces == ["default", "bridge0"]
    assert nets == ["default", "bridge0"]


def test_no_extras_no_change(render_vm) -> None:
    """Omitting all extras leaves the list lengths as base."""
    vm = render_vm(_base())
    spec = vm["spec"]["template"]["spec"]
    assert len(spec["domain"]["devices"]["disks"]) == 2  # containerdisk + cloudinitdisk
    assert len(spec["volumes"]) == 2
    assert len(spec["domain"]["devices"]["interfaces"]) == 1
    assert len(spec["networks"]) == 1
