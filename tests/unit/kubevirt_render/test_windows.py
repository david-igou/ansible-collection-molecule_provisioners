"""Tests for Windows-guest support (connection=psrp|winrm, sysprep_secret).

Windows goldens are sysprep-generalized clones: they have no cloud-init, boot
into OOBE, and are specialized by a KubeVirt sysprep volume (a Secret carrying
unattend.xml). Ansible then connects over WinRM-over-HTTPS (psrp/winrm, NTLM,
5986, cert ignore) as a local admin whose password the unattend set.

The renderer must, for a non-ssh connection:
  - render NO cloudinitdisk (a stray cloudinit disk shifts boot ordering);
  - when sysprep_secret is set, attach a sysprep cdrom disk + a volume whose
    field is `secret.name` (NOT `secretName`, which the API silently drops).

The runtime inventory + service must target 5986 for psrp/winrm hosts, and the
ssh path must remain byte-identical (regression).
"""

from __future__ import annotations

import json
import subprocess
import tempfile

from pathlib import Path

HERE = Path(__file__).parent
PORT_HARNESS = HERE / "ssh_port_render_harness.yml"
CONN_HARNESS = HERE / "connection_inventory_harness.yml"
GOLDEN_SSH = HERE / "golden_ssh_container_disk.json"


def _run_json(harness: Path, extra_vars: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "out.json"
        proc = subprocess.run(
            [
                "ansible-playbook",
                str(harness),
                "--extra-vars",
                json.dumps({**extra_vars, "output_path": str(output_path)}),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(output_path.read_text())


def _run_port(spec: dict) -> dict:
    return _run_json(PORT_HARNESS, {"spec": spec})


def _run_entry(spec: dict) -> dict:
    return _run_json(CONN_HARNESS, {"spec": spec})


# --- renderer: no cloudinit, sysprep present ---------------------------------

WIN_BOOT = {
    "type": "data_volume_source_ref",
    "source_ref": {"name": "win2k25", "namespace": "openshift-virtualization-os-images"},
    "size": "80Gi",
}


def _disk_names(vm: dict) -> list[str]:
    return [d["name"] for d in vm["spec"]["template"]["spec"]["domain"]["devices"]["disks"]]


def _volume_names(vm: dict) -> list[str]:
    return [v["name"] for v in vm["spec"]["template"]["spec"]["volumes"]]


def test_psrp_host_renders_no_cloudinitdisk(render_vm) -> None:
    vm = render_vm(
        {
            "boot_source": WIN_BOOT,
            "connection": "psrp",
            "admin_user": "Administrator",
            "admin_password": "S3cret!",
            "sysprep_secret": "win2k25-sysprep",
        },
    )
    assert "cloudinitdisk" not in _disk_names(vm)
    assert "cloudinitdisk" not in _volume_names(vm)
    # No cloud-init user-data anywhere in the rendered VM.
    assert "cloudInitNoCloud" not in json.dumps(vm)


def test_winrm_host_renders_no_cloudinitdisk(render_vm) -> None:
    vm = render_vm(
        {
            "boot_source": WIN_BOOT,
            "connection": "winrm",
            "admin_password": "S3cret!",
        },
    )
    assert "cloudinitdisk" not in _disk_names(vm)
    assert "cloudinitdisk" not in _volume_names(vm)


def test_psrp_sysprep_cdrom_disk_present(render_vm) -> None:
    vm = render_vm(
        {
            "boot_source": WIN_BOOT,
            "connection": "psrp",
            "admin_password": "S3cret!",
            "sysprep_secret": "win2k25-sysprep",
        },
    )
    disks = {d["name"]: d for d in vm["spec"]["template"]["spec"]["domain"]["devices"]["disks"]}
    assert "sysprep" in disks
    assert disks["sysprep"]["cdrom"]["bus"] == "sata"
    # Boot disk must remain first so boot ordering is unaffected.
    assert _disk_names(vm)[0] == "containerdisk"


def test_psrp_sysprep_volume_uses_secret_name(render_vm) -> None:
    """CRITICAL: the field is secret.name — secretName is silently dropped."""
    vm = render_vm(
        {
            "boot_source": WIN_BOOT,
            "connection": "psrp",
            "admin_password": "S3cret!",
            "sysprep_secret": "win2k25-sysprep",
        },
    )
    vol = next(v for v in vm["spec"]["template"]["spec"]["volumes"] if v["name"] == "sysprep")
    assert vol["sysprep"]["secret"]["name"] == "win2k25-sysprep"
    assert "secretName" not in json.dumps(vol)


def test_psrp_without_sysprep_has_no_sysprep_and_no_cloudinit(render_vm) -> None:
    """connection=psrp with no sysprep_secret: only the boot disk, no cdrom."""
    vm = render_vm(
        {
            "boot_source": WIN_BOOT,
            "connection": "psrp",
            "admin_password": "S3cret!",
        },
    )
    assert _disk_names(vm) == ["containerdisk"]
    assert _volume_names(vm) == ["containerdisk"]


def test_ssh_host_ignores_sysprep_secret_but_still_gets_it(render_vm) -> None:
    """sysprep_secret is valid for any connection; ssh keeps cloudinit AND sysprep."""
    vm = render_vm(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/x"},
            "sysprep_secret": "custom-answer",
        },
    )
    # ssh path keeps cloudinit; sysprep cdrom is appended after it.
    assert "cloudinitdisk" in _volume_names(vm)
    vol = next(v for v in vm["spec"]["template"]["spec"]["volumes"] if v["name"] == "sysprep")
    assert vol["sysprep"]["secret"]["name"] == "custom-answer"
    # Boot disk first, cloudinit next, sysprep last.
    assert _disk_names(vm)[0] == "containerdisk"


# --- ssh regression: byte-identical rendered VM ------------------------------


def test_ssh_render_byte_identical_to_golden(render_vm) -> None:
    """The ssh container_disk render must match the pre-Windows golden exactly.

    The golden was captured from origin/main before Windows support; comparing
    the parsed VM dict proves the ssh render path is unchanged.
    """
    vm = render_vm(
        {
            "boot_source": {
                "type": "container_disk",
                "image": "quay.io/containerdisks/ubuntu:24.04",
            },
            "ssh_user": "ubuntu",
            "cpu": {"cores": 4},
            "memory": "2Gi",
        },
    )
    golden = json.loads(GOLDEN_SSH.read_text())
    assert vm == golden


# --- port + service targeting 5986 -------------------------------------------


def test_psrp_none_mode_defaults_port_to_5986() -> None:
    result = _run_port({"connection": "psrp", "ssh_service": {"type": "None"}})
    assert result["ansible_port"] == 5986


def test_winrm_none_mode_defaults_port_to_5986() -> None:
    result = _run_port({"connection": "winrm", "ssh_service": {"type": "None"}})
    assert result["ansible_port"] == 5986


def test_psrp_none_mode_honors_port_override() -> None:
    result = _run_port(
        {"connection": "psrp", "ssh_service": {"type": "None", "port": 15986}},
    )
    assert result["ansible_port"] == 15986


def test_psrp_nodeport_mode_uses_service_nodeport() -> None:
    result = _run_port({"connection": "psrp", "ssh_service": {"type": "NodePort"}})
    assert result["ansible_port"] == 31234  # synthetic nodePort


# --- runtime inventory entry -------------------------------------------------


def test_psrp_inventory_entry() -> None:
    entry = _run_entry(
        {
            "connection": "psrp",
            "admin_user": "Administrator",
            "admin_password": "S3cret!",
            "ssh_service": {"type": "NodePort"},
        },
    )
    assert entry["ansible_connection"] == "psrp"
    assert entry["ansible_host"] == "10.9.9.9"
    assert entry["ansible_port"] == 31234
    assert entry["ansible_user"] == "Administrator"
    assert entry["ansible_password"] == "S3cret!"
    assert entry["ansible_psrp_auth"] == "ntlm"
    assert entry["ansible_psrp_cert_validation"] == "ignore"
    assert entry["ansible_psrp_protocol"] == "https"
    assert int(entry["ansible_psrp_connection_timeout"]) >= 30
    # No SSH artifacts leak into a psrp entry.
    assert "ansible_ssh_private_key_file" not in entry


def test_psrp_inventory_entry_defaults_admin_user() -> None:
    entry = _run_entry(
        {"connection": "psrp", "admin_password": "S3cret!", "ssh_service": {"type": "NodePort"}},
    )
    assert entry["ansible_user"] == "Administrator"


def test_winrm_inventory_entry() -> None:
    entry = _run_entry(
        {
            "connection": "winrm",
            "admin_password": "S3cret!",
            "ssh_service": {"type": "NodePort"},
        },
    )
    assert entry["ansible_connection"] == "winrm"
    assert entry["ansible_winrm_transport"] == "ntlm"
    assert entry["ansible_winrm_server_cert_validation"] == "ignore"
    assert entry["ansible_winrm_scheme"] == "https"
    assert entry["ansible_password"] == "S3cret!"


def test_ssh_inventory_entry_unchanged() -> None:
    entry = _run_entry(
        {"ssh_user": "ubuntu", "ssh_service": {"type": "NodePort"}},
    )
    assert entry["ansible_connection"] == "ssh"
    assert entry["ansible_user"] == "ubuntu"
    assert entry["ansible_ssh_private_key_file"] == "/tmp/identity_file"
    assert entry["ansible_port"] == 31234
    assert "ansible_password" not in entry


# --- validate: admin_password required for psrp/winrm ------------------------


def test_psrp_without_admin_password_fails_validate(run_validate) -> None:
    proc = run_validate(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/x"},
            "connection": "psrp",
        },
    )
    assert proc.returncode != 0
    assert "admin_password" in (proc.stdout + proc.stderr)


def test_psrp_with_admin_password_passes_validate(run_validate) -> None:
    proc = run_validate(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/x"},
            "connection": "psrp",
            "admin_password": "S3cret!",
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_invalid_connection_fails_validate(run_validate) -> None:
    proc = run_validate(
        {
            "boot_source": {"type": "container_disk", "image": "quay.io/x"},
            "connection": "telnet",
        },
    )
    assert proc.returncode != 0
    assert "telnet" in (proc.stdout + proc.stderr) or "connection" in (proc.stdout + proc.stderr)
