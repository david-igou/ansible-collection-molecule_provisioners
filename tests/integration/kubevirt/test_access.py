"""Real management and TCP/UDP application access through run-owned Services."""

import os
import socket
import time
import urllib.request

import pytest
import yaml

from test_run_isolation import Run, _object, _resources

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KUBEVIRT_ISOLATION") != "1",
    reason="requires explicitly enabled KubeVirt integration environment",
)
SERVER = """import http.server
import socket
import threading

def echo():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', 5353))
    while True:
        message, peer = sock.recvfrom(4096)
        sock.sendto(message, peer)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'molecule-application-ok')

threading.Thread(target=echo, daemon=True).start()
http.server.ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
"""


def _check_applications(entry):
    endpoints = entry["mp_kubevirt_endpoints"]
    http = endpoints["http"]
    for _attempt in range(60):
        try:
            with urllib.request.urlopen(
                f"http://{http['host']}:{http['port']}/", timeout=5
            ) as response:
                assert response.read() == b"molecule-application-ok"
            break
        except OSError:
            time.sleep(2)
    else:
        pytest.fail("HTTP endpoint never became reachable")
    if "echo" in endpoints:
        udp = endpoints["echo"]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(10)
            client.sendto(b"molecule-udp-ok", (udp["host"], udp["port"]))
            assert client.recv(4096) == b"molecule-udp-ok"


def test_custom_ssh_port_application_endpoints_and_service_updates(tmp_path):
    resources = _resources()
    namespace = os.environ.get("MOLECULE_NAMESPACE", "molecule")
    run = Run(tmp_path / "access", namespace)
    inventory = yaml.safe_load(run.inventory.read_text())
    spec = inventory["all"]["children"]["molecule"]["hosts"]["instance"]["mp"]["kubevirt"]
    spec.update(
        {
            "ssh_service": {"type": "NodePort", "port": 2222},
            "connection_vars": {
                "ansible_ssh_common_args": "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30"
            },
            "application_ports": [
                {"name": "http", "port": 80, "target_port": 8080},
                {"name": "echo", "port": 5353, "protocol": "UDP"},
            ],
            "cloud_init": {
                "user_data": {
                    "write_files": [
                        {
                            "path": "/etc/ssh/sshd_config.d/50-molecule.conf",
                            "content": "Port 2222\n",
                        },
                        {"path": "/usr/local/bin/mp-test-server.py", "content": SERVER},
                    ],
                    "runcmd": [
                        ["systemctl", "disable", "--now", "ssh.socket"],
                        ["systemctl", "restart", "ssh"],
                        [
                            "sh",
                            "-c",
                            "nohup python3 /usr/local/bin/mp-test-server.py >/tmp/mp-test-server.log 2>&1 &",
                        ],
                    ],
                }
            },
        }
    )
    run.inventory.write_text(yaml.safe_dump(inventory, default_flow_style=False))
    try:
        run.play("create")
        run.verify_guest()
        runtime_path = run.ephemeral / "inventory" / "molecule_runtime.yml"
        entry = yaml.safe_load(runtime_path.read_text())["all"]["hosts"]["instance"]
        assert (
            entry["ansible_ssh_common_args"] == spec["connection_vars"]["ansible_ssh_common_args"]
        )
        service = _object(resources, run, "Service")
        ports = {port.name: port for port in service.spec.ports}
        assert ports["management"].targetPort == 2222
        assert service.spec.selector["kubevirt.io/domain"] == run.state["hosts"]["instance"]["name"]
        _check_applications(entry)
        run.play("create")
        repeated = yaml.safe_load(runtime_path.read_text())["all"]["hosts"]["instance"]
        assert repeated == entry
        spec["application_ports"] = spec["application_ports"][:1]
        run.inventory.write_text(yaml.safe_dump(inventory, default_flow_style=False))
        run.play("create")
        remaining = yaml.safe_load(runtime_path.read_text())["all"]["hosts"]["instance"]
        assert remaining["mp_kubevirt_endpoints"]["http"] == entry["mp_kubevirt_endpoints"]["http"]
        assert remaining["ansible_port"] == entry["ansible_port"]
        assert {port.name for port in _object(resources, run, "Service").spec.ports} == {
            "management",
            "http",
        }
        _check_applications(remaining)
        name = run.state["hosts"]["instance"]["name"]
        run.play("destroy")
        from kubernetes.dynamic.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            resources.get(api_version="v1", kind="Service").get(name=name, namespace=namespace)
        run.play("destroy")
    finally:
        if run.state_path.exists():
            run.play("destroy")
