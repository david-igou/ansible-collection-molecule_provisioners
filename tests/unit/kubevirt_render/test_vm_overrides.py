"""Renderer tests for the vm_overrides escape hatch (deep-merge, list-append)."""

from __future__ import annotations


def _base(extra: dict | None = None) -> dict:
    spec: dict = {"boot_source": {"type": "container_disk", "image": "quay.io/x"}}
    if extra:
        spec.update(extra)
    return spec


def test_overrides_metadata_labels(render_vm) -> None:
    """vm_overrides.metadata.labels deep-merges into metadata.labels."""
    vm = render_vm(_base({"vm_overrides": {"metadata": {"labels": {"team": "platform"}}}}))
    labels = vm["metadata"]["labels"]
    assert labels["team"] == "platform"
    assert labels["kubevirt.io/domain"] == "instance"  # base label preserved


def test_overrides_annotations(render_vm) -> None:
    vm = render_vm(_base({"vm_overrides": {"metadata": {"annotations": {"foo": "bar"}}}}))
    assert vm["metadata"]["annotations"] == {"foo": "bar"}


def test_overrides_termination_grace_period(render_vm) -> None:
    vm = render_vm(
        _base(
            {
                "vm_overrides": {
                    "spec": {"template": {"spec": {"terminationGracePeriodSeconds": 60}}}
                },
            }
        )
    )
    assert vm["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 60


def test_overrides_tolerations_list_append(render_vm) -> None:
    """A toleration from vm_overrides appends to a toleration from the curated field."""
    curated_tol = {"key": "curated", "operator": "Exists", "effect": "NoSchedule"}
    override_tol = {"key": "override", "operator": "Exists", "effect": "NoSchedule"}
    vm = render_vm(
        _base(
            {
                "tolerations": [curated_tol],
                "vm_overrides": {"spec": {"template": {"spec": {"tolerations": [override_tol]}}}},
            }
        )
    )
    tols = vm["spec"]["template"]["spec"]["tolerations"]
    assert curated_tol in tols
    assert override_tol in tols


def test_overrides_firmware(render_vm) -> None:
    """vm_overrides can reach into nested domain fields like firmware."""
    vm = render_vm(
        _base(
            {
                "vm_overrides": {
                    "spec": {
                        "template": {
                            "spec": {
                                "domain": {
                                    "firmware": {"bootloader": {"efi": {"secureBoot": False}}}
                                }
                            }
                        }
                    }
                },
            }
        )
    )
    fw = vm["spec"]["template"]["spec"]["domain"]["firmware"]
    assert fw["bootloader"]["efi"]["secureBoot"] is False


def test_overrides_scalar_replaces_base_value(render_vm) -> None:
    """vm_overrides scalar (e.g., spec.running) replaces the base value.

    The renderer's base sets spec.running: true so the prepare phase can SSH in.
    Per the no-guardrails design, users CAN override this — the README documents
    it as a foot-gun, but the renderer doesn't try to protect them.
    """
    vm = render_vm(_base({"vm_overrides": {"spec": {"running": False}}}))
    assert vm["spec"]["running"] is False
