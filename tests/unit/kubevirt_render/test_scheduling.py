"""Renderer tests for scheduling knobs (nodeSelector, tolerations, affinity)."""

from __future__ import annotations


def _base(extra: dict | None = None) -> dict:
    spec: dict = {"boot_source": {"type": "container_disk", "image": "quay.io/x"}}
    if extra:
        spec.update(extra)
    return spec


def test_no_scheduling_keys_when_unset(render_vm) -> None:
    spec = render_vm(_base())["spec"]["template"]["spec"]
    assert "nodeSelector" not in spec
    assert "tolerations" not in spec
    assert "affinity" not in spec


def test_node_selector(render_vm) -> None:
    vm = render_vm(_base({"node_selector": {"kubernetes.io/arch": "amd64", "role": "test"}}))
    assert vm["spec"]["template"]["spec"]["nodeSelector"] == {
        "kubernetes.io/arch": "amd64",
        "role": "test",
    }


def test_tolerations(render_vm) -> None:
    tol = [{"key": "dedicated", "operator": "Equal", "value": "molecule", "effect": "NoSchedule"}]
    vm = render_vm(_base({"tolerations": tol}))
    assert vm["spec"]["template"]["spec"]["tolerations"] == tol


def test_affinity_passthrough(render_vm) -> None:
    aff = {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {"matchExpressions": [{"key": "k", "operator": "In", "values": ["v"]}]},
                ],
            },
        },
    }
    vm = render_vm(_base({"affinity": aff}))
    assert vm["spec"]["template"]["spec"]["affinity"] == aff
