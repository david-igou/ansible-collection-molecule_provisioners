"""Tests for roles/kubevirt/tasks/_validate.yml (negative paths)."""

from __future__ import annotations


def test_valid_container_disk_passes(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "container_disk", "image": "quay.io/x"}})
    assert proc.returncode == 0, proc.stderr


def test_missing_boot_source_fails(run_validate) -> None:
    proc = run_validate({"namespace": "test"})  # no boot_source
    assert proc.returncode != 0
    assert "boot_source" in (proc.stdout + proc.stderr)


def test_invalid_type_fails(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "wat", "image": "x"}})
    assert proc.returncode != 0
    assert "wat" in (proc.stdout + proc.stderr) or "container_disk" in (proc.stdout + proc.stderr)


def test_container_disk_missing_image_fails(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "container_disk"}})
    assert proc.returncode != 0
    assert "image" in (proc.stdout + proc.stderr)


def test_data_volume_url_missing_size_fails(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "data_volume_url", "url": "https://x"}})
    assert proc.returncode != 0
    assert "size" in (proc.stdout + proc.stderr)


def test_data_volume_pvc_missing_source_namespace_fails(run_validate) -> None:
    proc = run_validate(
        {
            "boot_source": {"type": "data_volume_pvc", "source": {"name": "g"}, "size": "10Gi"},
        }
    )
    assert proc.returncode != 0
    assert "namespace" in (proc.stdout + proc.stderr)


def test_pvc_missing_name_fails(run_validate) -> None:
    proc = run_validate({"boot_source": {"type": "pvc"}})
    assert proc.returncode != 0
    assert "name" in (proc.stdout + proc.stderr)


def test_invalid_ssh_service_type_fails(run_validate) -> None:
    proc = run_validate(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/x"},
            "ssh_service": {"type": "LoadBalancer"},
        }
    )
    assert proc.returncode != 0
    assert "LoadBalancer" in (proc.stdout + proc.stderr) or "NodePort" in (
        proc.stdout + proc.stderr
    )
