"""Storage and access contracts for testing guests."""

from pathlib import Path

import pytest

from .test_windows import _run_json

HERE = Path(__file__).parent
BOOT = {"type": "container_disk", "image": "quay.io/x"}
PORTS = [
    {"name": "http", "port": 80, "target_port": 8080},
    {"name": "dns", "port": 53, "protocol": "UDP"},
]


@pytest.mark.parametrize("mode", ["NodePort", "None", "PodIP"])
def test_guest_port_and_connection_options(mode):
    entry = _run_json(
        HERE / "connection_inventory_harness.yml",
        {
            "spec": {
                "ssh_service": {"type": mode, "port": 2222},
                "connection_ip": "192.0.2.1",
                "application_ports": PORTS,
                "connection_vars": {
                    "ansible_ssh_common_args": "-o ProxyJump=bastion",
                    "ansible_ssh_private_key_file": "/tmp/existing-key",
                },
            }
        },
    )
    assert entry["ansible_port"] == (31234 if mode == "NodePort" else 2222)
    assert isinstance(entry["ansible_port"], int)
    assert entry["ansible_ssh_common_args"] == "-o ProxyJump=bastion"
    assert entry["ansible_ssh_private_key_file"] == "/tmp/existing-key"
    endpoints = entry["mp_kubevirt_endpoints"]
    assert endpoints["http"]["port"] == (31235 if mode == "NodePort" else 8080)
    assert endpoints["dns"]["port"] == (31236 if mode == "NodePort" else 53)
    assert endpoints["dns"]["protocol"] == "UDP"
    assert endpoints["http"]["host"] == ("10.130.0.42" if mode == "PodIP" else "192.0.2.1")


@pytest.mark.parametrize("connection", ["ssh", "psrp", "winrm"])
def test_service_honors_management_port(connection):
    service = _run_json(
        HERE / "service_harness.yml",
        {
            "spec": {
                "connection": connection,
                "ssh_service": {"port": 2222},
                "application_ports": PORTS,
            },
            "__mp_kubevirt_run": {
                "id": "run-one",
                "hosts": {"instance": {"name": "instance-run-one"}},
            },
        },
    )
    assert service["spec"]["selector"] == {"kubevirt.io/domain": "instance-run-one"}
    assert service["metadata"]["labels"]["molecule-provisioners.igou.io/run-id"] == "run-one"
    ports = {p["name"]: p for p in service["spec"]["ports"]}
    assert ports["management"]["port"] == ports["management"]["targetPort"] == 2222
    assert ports["http"]["port"] == 80 and ports["http"]["targetPort"] == 8080
    assert ports["dns"]["protocol"] == "UDP"


def test_windows_connection_overrides(run_validate):
    spec = {
        "boot_source": BOOT,
        "connection": "psrp",
        "connection_vars": {
            "ansible_password": "dummy-secret",
            "ansible_psrp_auth": "basic",
            "ansible_psrp_cert_validation": "validate",
            "ansible_user": "test-admin",
        },
    }
    proc = run_validate(spec)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "dummy-secret" not in proc.stdout + proc.stderr
    entry = _run_json(HERE / "connection_inventory_harness.yml", {"spec": spec})
    assert entry["ansible_psrp_auth"] == "basic"
    assert entry["ansible_user"] == "test-admin"
    assert entry["ansible_password"] == "dummy-secret"
    assert entry["ansible_psrp_cert_validation"] == "validate"


@pytest.mark.parametrize("connection", ["psrp", "winrm"])
@pytest.mark.parametrize("auth", ["kerberos", "certificate"])
def test_explicit_passwordless_windows_auth(run_validate, connection, auth):
    field = "ansible_psrp_auth" if connection == "psrp" else "ansible_winrm_transport"
    proc = run_validate(
        {"boot_source": BOOT, "connection": connection, "connection_vars": {field: auth}}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_storage_options_and_managed_disk_identities(render_vm):
    spec = {
        "boot_source": {
            "type": "data_volume_url",
            "url": "https://example.com/boot.qcow2",
            "size": "4Gi",
            "volume_mode": "Block",
            "access_modes": ["ReadWriteMany"],
        },
        "boot_disk": {"bus": "sata", "boot_order": 1},
        "data_disks": [
            {
                "name": "data",
                "size": "2Gi",
                "bus": "scsi",
                "boot_order": 2,
                "volume_mode": "Filesystem",
                "access_modes": ["ReadWriteOnce"],
            },
            {
                "name": "clone",
                "size": "3Gi",
                "source_ref": {"name": "golden", "namespace": "images"},
            },
        ],
        "extra_disks": [{"name": "external", "disk": {"bus": "virtio"}}],
        "extra_volumes": [{"name": "external", "persistentVolumeClaim": {"claimName": "keep-me"}}],
    }
    run = {
        "id": "run-one",
        "hosts": {"instance": {"name": "instance-run-one", "namespace": "molecule"}},
    }
    vm = render_vm(spec, run)
    guest = vm["spec"]["template"]["spec"]
    disks = {d["name"]: d for d in guest["domain"]["devices"]["disks"]}
    assert disks["containerdisk"]["disk"]["bus"] == "sata"
    assert disks["containerdisk"]["bootOrder"] == 1
    assert disks["data"]["disk"]["bus"] == "scsi"
    assert disks["data"]["bootOrder"] == 2
    templates = vm["spec"]["dataVolumeTemplates"]
    assert len(templates) == 3
    assert templates[0]["spec"]["storage"]["volumeMode"] == "Block"
    assert templates[0]["spec"]["storage"]["accessModes"] == ["ReadWriteMany"]
    assert templates[1]["spec"]["source"] == {"blank": {}}
    assert templates[2]["spec"]["sourceRef"] == {
        "name": "golden",
        "namespace": "images",
        "kind": "DataSource",
    }
    names = {d["metadata"]["name"] for d in templates}
    assert len(names) == 3
    assert all(
        d["metadata"]["labels"]["molecule-provisioners.igou.io/run-id"] == "run-one"
        for d in templates
    )
    assert all(v["dataVolume"]["name"] in names for v in guest["volumes"] if "dataVolume" in v)
    assert (
        next(v for v in guest["volumes"] if v["name"] == "external")["persistentVolumeClaim"][
            "claimName"
        ]
        == "keep-me"
    )
    other = render_vm(
        spec,
        {
            "id": "run-two",
            "hosts": {"instance": {"name": "instance-run-two", "namespace": "molecule"}},
        },
    )
    assert names.isdisjoint(d["metadata"]["name"] for d in other["spec"]["dataVolumeTemplates"])


@pytest.mark.parametrize(
    "patch",
    [
        {"connection_vars": {"ansible_host": "example.com"}},
        {"connection_vars": {"ansible_psrp_port": 1234}},
        {"ssh_service": {"port": 0}},
        {"application_ports": [{"name": "management", "port": 80}]},
        {"application_ports": [{"name": "http", "port": 22}]},
        {"application_ports": [{"name": "http", "port": 80}, {"name": "other", "port": 80}]},
        {"application_ports": [{"name": "bad_name", "port": 80}]},
        {"boot_disk": {"boot_order": 0}},
        {"data_disks": [{"name": "containerdisk", "size": "1Gi"}]},
        {
            "data_disks": [
                {
                    "name": "data",
                    "size": "1Gi",
                    "source": {"blank": {}},
                    "source_ref": {"name": "x", "namespace": "images"},
                }
            ]
        },
        {
            "boot_disk": {"boot_order": 1},
            "data_disks": [{"name": "data", "size": "1Gi", "boot_order": 1}],
        },
    ],
)
def test_invalid_testing_options_fail_before_provisioning(run_validate, patch):
    proc = run_validate({"boot_source": BOOT, **patch})
    assert proc.returncode != 0, proc.stdout + proc.stderr
