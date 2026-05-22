"""Renderer tests for the cpu / memory / memory_limit curated knobs."""

from __future__ import annotations


def _base(extra: dict | None = None) -> dict:
    spec: dict = {"boot_source": {"type": "container_disk", "image": "quay.io/x"}}
    if extra:
        spec.update(extra)
    return spec


def test_cpu_defaults_to_two_cores(render_vm) -> None:
    """No cpu key in input → renders cpu.cores=2 (matches the v1.0 behavior)."""
    vm = render_vm(_base())
    assert vm["spec"]["template"]["spec"]["domain"]["cpu"] == {"cores": 2}


def test_cpu_full(render_vm) -> None:
    """All cpu sub-keys pass through verbatim."""
    vm = render_vm(
        _base({"cpu": {"cores": 4, "sockets": 2, "threads": 1, "model": "host-passthrough"}})
    )
    assert vm["spec"]["template"]["spec"]["domain"]["cpu"] == {
        "cores": 4,
        "sockets": 2,
        "threads": 1,
        "model": "host-passthrough",
    }


def test_memory_default_request(render_vm) -> None:
    """No memory key → resources.requests.memory='1Gi' (the role default)."""
    vm = render_vm(_base())
    res = vm["spec"]["template"]["spec"]["domain"]["resources"]
    assert res["requests"]["memory"] == "1Gi"
    assert "limits" not in res


def test_memory_explicit_and_limit(render_vm) -> None:
    """memory + memory_limit render both requests and limits."""
    vm = render_vm(_base({"memory": "2Gi", "memory_limit": "4Gi"}))
    res = vm["spec"]["template"]["spec"]["domain"]["resources"]
    assert res["requests"]["memory"] == "2Gi"
    assert res["limits"]["memory"] == "4Gi"


def test_memory_explicit_no_limit(render_vm) -> None:
    """Explicit memory without memory_limit sets request but no limit."""
    vm = render_vm(_base({"memory": "2Gi"}))
    res = vm["spec"]["template"]["spec"]["domain"]["resources"]
    assert res["requests"]["memory"] == "2Gi"
    assert "limits" not in res


def test_instancetype_string_shortcut(render_vm) -> None:
    """instancetype as a string becomes {name: <str>} at spec.instancetype."""
    vm = render_vm(_base({"instancetype": "u1.medium"}))
    assert vm["spec"]["instancetype"] == {"name": "u1.medium"}


def test_instancetype_full_form(render_vm) -> None:
    """instancetype as {name, kind} passes through verbatim."""
    vm = render_vm(
        _base({"instancetype": {"name": "u1.medium", "kind": "VirtualMachineInstancetype"}})
    )
    assert vm["spec"]["instancetype"] == {
        "name": "u1.medium",
        "kind": "VirtualMachineInstancetype",
    }


def test_preference_string_shortcut(render_vm) -> None:
    """preference as a string becomes {name: <str>}."""
    vm = render_vm(_base({"instancetype": "u1.medium", "preference": "fedora"}))
    assert vm["spec"]["preference"] == {"name": "fedora"}


def test_instancetype_suppresses_cpu_and_resources(render_vm) -> None:
    """When instancetype is set, domain.cpu and domain.resources are absent."""
    vm = render_vm(_base({"instancetype": "u1.medium", "cpu": {"cores": 8}, "memory": "16Gi"}))
    domain = vm["spec"]["template"]["spec"]["domain"]
    assert "cpu" not in domain
    assert "resources" not in domain


def test_no_instancetype_no_spec_instancetype_key(render_vm) -> None:
    """Without instancetype, spec.instancetype is not rendered."""
    vm = render_vm(_base())
    assert "instancetype" not in vm["spec"]
    assert "preference" not in vm["spec"]


def test_preference_alone_without_instancetype(render_vm) -> None:
    """preference without instancetype: spec.preference renders; cpu/resources still present."""
    vm = render_vm(_base({"preference": "fedora"}))
    assert vm["spec"]["preference"] == {"name": "fedora"}
    assert "instancetype" not in vm["spec"]
    domain = vm["spec"]["template"]["spec"]["domain"]
    assert "cpu" in domain
    assert "resources" in domain
