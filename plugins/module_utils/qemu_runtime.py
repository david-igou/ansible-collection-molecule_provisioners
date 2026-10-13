# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT
"""Linux QEMU ownership, QMP observation and locked run reservations."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def atomic_write(path: str | Path, value: dict) -> None:
    """Replace private state atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".qemu-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: str | Path, default: dict) -> dict:
    """Read state, refusing to silently reset corrupted manifests."""
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default


def qmp(path: str, command: str) -> dict:
    """Perform one QMP command after negotiating capabilities."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(path)
        with client.makefile("rwb") as stream:
            greeting = json.loads(stream.readline())
            if "QMP" not in greeting:
                raise ValueError("Invalid QMP greeting")
            for index, execute in enumerate(("qmp_capabilities", command)):
                stream.write((json.dumps({"execute": execute, "id": index}) + "\n").encode())
                stream.flush()
                while True:
                    response = json.loads(stream.readline())
                    if response.get("id") == index:
                        break
                if "error" in response:
                    raise ValueError("QMP command failed: " + command)
            return response["return"]


def process(pid: int) -> dict | None:
    """Observe identity and ignore exited/zombie processes."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {
            "pid": int(pid),
            "start_time": stat[19],
            "executable": os.readlink(f"/proc/{pid}/exe"),
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


def owned(record: dict) -> bool:
    """Require both Linux process identity and the VM's QMP UUID."""
    current = process(record.get("pid", 0))
    if current is None:
        return False
    if any(current[key] != record.get(key) for key in ("pid", "start_time", "executable")):
        raise ValueError("Process identity changed; refusing to signal an unrelated process.")
    if qmp(record["qmp"], "query-uuid")["UUID"] != record["vm_uuid"]:
        raise ValueError("QMP UUID does not match; refusing to signal an unrelated VM.")
    return True


class Runtime:
    """Manage one manifest under a controller-wide reservation lock."""

    def __init__(self, directory: str | Path, cache: str | Path) -> None:
        self.directory = Path(directory).absolute()
        if any(character in str(self.directory) for character in (",", "\n")):
            raise ValueError("QEMU run directory must not contain commas or newlines.")
        self.path = self.directory / "qemu-state.json"
        self.cache = Path(cache).absolute()
        self.reservations_path = self.cache / "qemu-reservations.json"
        self.state = read_json(self.path, {})
        self.owner_uuid = self.state.get("run_uuid")
        self.reservations = {}

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialize allocation, launch and cleanup across scenario runs."""
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.cache / "qemu-runtime.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.state = read_json(self.path, {})
            self.owner_uuid = self.state.get("run_uuid")
            self.reservations = read_json(self.reservations_path, {})
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def save(self) -> None:
        """Persist manifest before publishing global reservations."""
        atomic_write(self.path, self.state)
        atomic_write(self.reservations_path, self.reservations)

    def allocate(
        self, requested: str | int, protocol: str = "TCP", address: str = "127.0.0.1"
    ) -> int:
        """Reserve an available port, avoiding both allocated and occupied ports."""
        for attempt in range(100):
            with socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM if protocol == "UDP" else socket.SOCK_STREAM,
            ) as listener:
                try:
                    listener.bind((address, 0 if requested == "auto" else requested))
                except OSError as error:
                    raise ValueError(
                        f"Requested {protocol} port {requested} on {address} is occupied."
                    ) from error
                chosen = listener.getsockname()[1]
            key = f"{protocol}:{address}:{chosen}"
            conflict = any(
                k.split(":")[0] == protocol
                and k.split(":")[2] == str(chosen)
                and (address == "0.0.0.0" or k.split(":")[1] in (address, "0.0.0.0"))
                for k in self.reservations
            )
            if not conflict:
                self.reservations[key] = {
                    "manifest": str(self.path),
                    "run_uuid": self.state["run_uuid"],
                }
                return chosen
            if requested != "auto":
                raise ValueError("Requested port is already reserved by a QEMU run: " + key)
        raise ValueError("Unable to reserve an automatic port after 100 attempts.")

    def reserve(self, hosts: dict, isolation: bool = True, base: int = 2222) -> bool:
        """Allocate run-owned artifacts and endpoints before side effects."""
        if not isolation and not 1 <= base <= 65536 - len(hosts):
            raise ValueError("Legacy port base plus guest count must fit in 1..65535.")
        for host, request in hosts.items():
            names = [host] + request.get("data_disks", [])
            if any(
                not isinstance(value, str)
                or Path(value).name != value
                or value in (".", "..")
                or "," in value
                or "\n" in value
                for value in names
            ):
                raise ValueError("Guest and disk names must be safe single path components.")
            allowed = {
                "fingerprint",
                "ssh_host_port",
                "ssh_port",
                "application_ports",
                "data_disks",
                "networks",
                "nics",
                "diagnostics",
            }
            if set(request) - allowed:
                raise ValueError(
                    "Unexpected allocation fields; use role-generated non-secret requests."
                )
        if self.state:
            if self.state.get("phase") == "destroying":
                raise ValueError(
                    "Cleanup is incomplete; retry destroy before creating this run again."
                )
            policy = {
                "isolation": isolation,
                "port_base": base if not isolation else None,
            }
            if self.state.get("port_policy") != policy:
                raise ValueError("Run port policy changed; destroy/create is required.")
            if set(hosts) != set(self.state["hosts"]):
                raise ValueError("Guest inventory changed; destroy the existing run before create.")
            for host, request in hosts.items():
                if request["fingerprint"] != self.state["hosts"][host]["fingerprint"]:
                    raise ValueError(
                        f"Boot configuration changed for {host}; destroy/create is required."
                    )
            changed = False
            for host, request in hosts.items():
                record = self.state["hosts"][host]
                if record["diagnostics"] != request["diagnostics"]:
                    record["diagnostics"] = request["diagnostics"]
                    record["requested"] = request
                    changed = True
            if changed:
                self.save()
            return changed
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never adopt pre-manifest guests: pidfiles are migration evidence.
        if list(self.directory.glob("*.pid")):
            raise ValueError(
                "Legacy QEMU pidfiles found. Destroy with the previous provisioner before upgrading."
            )
        self.state = {
            "version": 1,
            "run_uuid": str(uuid.uuid4()),
            "hosts": {},
            "networks": {},
            "launch_order": list(hosts),
            "port_policy": {
                "isolation": isolation,
                "port_base": base if not isolation else None,
            },
        }
        self.owner_uuid = self.state["run_uuid"]
        for index, (host, request) in enumerate(hosts.items()):
            stem = str(self.directory / host)
            record = {
                "logical_name": host,
                "physical_name": "mp-{}-{}".format(
                    self.state["run_uuid"].replace("-", "")[:12], host
                ),
                "vm_uuid": str(uuid.uuid4()),
                "fingerprint": request["fingerprint"],
                "pidfile": stem + ".pid",
                "qmp": stem + ".qmp",
                "overlay": stem + ".qcow2",
                "seed_iso": stem + "-seed.iso",
                "cidata": stem + "-cidata",
                "ovmf_vars": stem + "-ovmf-vars.fd",
                "serial_log": stem + "-serial.log",
                "stderr_log": stem + "-stderr.log",
                "diagnostics": request["diagnostics"],
                "endpoints": {},
                "disks": {},
                "networks": {},
                "status": "reserved",
                "auto_ssh_port": isolation and request["ssh_host_port"] == "auto",
                "requested": request,
            }
            if len(os.fsencode(record["qmp"])) >= 108:
                raise ValueError(
                    "Ephemeral path is too long for a QMP socket; choose a shorter directory."
                )
            record["ssh_port"] = self.allocate(
                request["ssh_host_port"]
                if isolation or request["ssh_host_port"] != "auto"
                else base + index
            )
            for endpoint in request["application_ports"]:
                record["endpoints"][endpoint["name"]] = {
                    "host": endpoint["bind_address"],
                    "port": self.allocate(
                        endpoint["host_port"],
                        endpoint["protocol"],
                        endpoint["bind_address"],
                    ),
                    "guest_port": endpoint["port"],
                    "protocol": endpoint["protocol"],
                }
            record["disks"] = {
                disk: stem + "-disk-" + disk + ".qcow2" for disk in request["data_disks"]
            }
            self.state["hosts"][host] = record
        for host, request in hosts.items():
            for network in request["networks"]:
                key = network["name"]
                if key not in self.state["networks"]:
                    link = dict(network)
                    if link["type"] == "socket":
                        peers = [
                            h
                            for h, r in hosts.items()
                            if any(n["network"] == key for n in r["nics"])
                        ]
                        link.update(listener=peers[0], port=self.allocate("auto"))
                    self.state["networks"][key] = link
                self.state["hosts"][host]["networks"][key] = self.state["networks"][key]
        self.save()
        return True

    def recover_launch(self, record: dict) -> None:
        """Recover a daemon whose launch completed before PID state was saved."""
        if record["status"] != "launching" or not Path(record["pidfile"]).is_file():
            return
        pid = int(Path(record["pidfile"]).read_text().strip())
        current = process(pid)
        if current is None:
            record["status"] = "stopped"
            record.pop("pid", None)
            record.pop("start_time", None)
            self.save()
            return
        if (
            current
            and current["executable"] == record.get("executable")
            and qmp(record["qmp"], "query-uuid")["UUID"] == record["vm_uuid"]
        ):
            record.update(current, status="running")
            self.save()
        elif current:
            raise ValueError("Uncertain interrupted launch ownership; retaining recovery state.")

    def retry_ports(self, record: dict, argv: list[str]) -> list[str]:
        """Replace automatic allocations after a non-cooperating bind race."""
        replacements = []
        request = record["requested"]
        fixed = []
        if not record["auto_ssh_port"]:
            fixed.append(("TCP", "127.0.0.1", record["ssh_port"]))
        for endpoint in request["application_ports"]:
            if endpoint["host_port"] != "auto":
                fixed.append(
                    (
                        endpoint["protocol"],
                        endpoint["bind_address"],
                        endpoint["host_port"],
                    )
                )
        for protocol, address, number in fixed:
            with socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM if protocol == "UDP" else socket.SOCK_STREAM,
            ) as probe:
                try:
                    probe.bind((address, number))
                except OSError as error:
                    raise ValueError(
                        f"Explicit {protocol} port {number} on {address} became occupied; refusing automatic replacement."
                    ) from error
        if record["auto_ssh_port"]:
            old = record["ssh_port"]
            record["ssh_port"] = self.allocate("auto")
            replacements.append(
                (
                    f"tcp:127.0.0.1:{old}-:",
                    "tcp:127.0.0.1:{}-:".format(record["ssh_port"]),
                )
            )
        for item in request["application_ports"]:
            if item["host_port"] == "auto":
                endpoint = record["endpoints"][item["name"]]
                old = endpoint["port"]
                endpoint["port"] = self.allocate("auto", item["protocol"], item["bind_address"])
                replacements.append(
                    (
                        "{}:{}:{}-:".format(item["protocol"].lower(), endpoint["host"], old),
                        "{}:{}:{}-:".format(
                            item["protocol"].lower(), endpoint["host"], endpoint["port"]
                        ),
                    )
                )
        for key, network in self.state["networks"].items():
            if network["type"] == "socket" and network["listener"] == record["logical_name"]:
                old = network["port"]
                network["port"] = self.allocate("auto")
                replacements.append(
                    (
                        f"listen=127.0.0.1:{old}",
                        "listen=127.0.0.1:{}".format(network["port"]),
                    )
                )
                for host in self.state["hosts"].values():
                    if key in host["networks"]:
                        host["networks"][key] = dict(network)
        if not replacements:
            raise ValueError(
                "Requested explicit port became occupied; no automatic retry is possible."
            )
        for old, new in replacements:
            argv = [arg.replace(old, new) for arg in argv]
        # Rebuild reservations from current persisted allocations, dropping replaced ports.
        self.release_unused()
        self.save()
        return argv

    def release_unused(self) -> None:
        """Release this run's superseded or stopped allocations only."""
        active = set()
        for record in self.state.get("hosts", {}).values():
            if record["status"] == "removed":
                continue
            active.add("TCP:127.0.0.1:{}".format(record["ssh_port"]))
            for endpoint in record["endpoints"].values():
                active.add(
                    "{}:{}:{}".format(endpoint["protocol"], endpoint["host"], endpoint["port"])
                )
            for network in record["networks"].values():
                if network["type"] == "socket":
                    active.add("TCP:127.0.0.1:{}".format(network["port"]))
        self.reservations = {
            k: v
            for k, v in self.reservations.items()
            if v["manifest"] != str(self.path)
            or v.get("run_uuid") != self.owner_uuid
            or k in active
        }

    def launch(self, host: str, argv: list[str]) -> bool:
        """Start one role-configured guest, verifying UUID before reuse."""
        record = self.state["hosts"][host]
        self.recover_launch(record)
        if record.get("pid") and owned(record):
            return False
        for key in ("pidfile", "qmp"):
            Path(record[key]).unlink(missing_ok=True)
        record["executable"] = os.path.realpath(shutil.which(argv[0]) or argv[0])
        record["status"] = "launching"
        self.save()
        for attempt in range(3):
            fd = os.open(record["stderr_log"], os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(fd, "ab") as log:
                result = subprocess.run(argv, stdout=log, stderr=log, check=False)
            if result.returncode == 0:
                os.chmod(record["qmp"], 0o600)
                self.recover_launch(record)
                if not record.get("pid") or not owned(record):
                    raise ValueError(
                        "QEMU exited before its identity could be verified; see "
                        + record["stderr_log"]
                    )
                status = qmp(record["qmp"], "query-status")["status"]
                if status != "running" and "-S" not in argv:
                    raise ValueError(
                        "QEMU is not running ({}); see {}".format(status, record["stderr_log"])
                    )
                return True
            error = Path(record["stderr_log"]).read_text(errors="replace")[-8192:]
            if attempt < 2 and ("host forwarding" in error or "Address already in use" in error):
                argv = self.retry_ports(record, argv)
            else:
                raise ValueError("QEMU launch failed; private stderr log: " + record["stderr_log"])
        return False

    def diagnostics(self, host: str | None, print_tail: bool = False) -> dict:
        """Return process status and file paths; console contents require opt-in."""
        record = self.state.get("hosts", {}).get(host)
        if not record:
            return {"status": "no owned state"}
        result = {
            "status": "exited",
            "serial_log": record["serial_log"],
            "stderr_log": record["stderr_log"],
            "ssh_host": "127.0.0.1",
            "ssh_port": record["ssh_port"],
        }
        if record.get("pid") and process(record["pid"]):
            try:
                owned(record)
                result["status"] = qmp(record["qmp"], "query-status")["status"]
            except (OSError, ValueError):
                result["status"] = "ownership unverified"
        if print_tail:
            for key in ("serial_log", "stderr_log"):
                path = Path(record[key])
                if path.is_file():
                    # Read only the end of potentially large logs.
                    with path.open("rb") as stream:
                        stream.seek(max(0, path.stat().st_size - 65536))
                        result[key + "_tail"] = "\n".join(
                            stream.read()
                            .decode(errors="replace")
                            .splitlines()[-record["diagnostics"]["failure_tail_lines"] :]
                        )
        return result

    def destroy(self, timeout: int = 30) -> bool:
        """Stop all manifest-owned guests before deleting their resources."""
        changed = False
        errors = []
        if self.state:
            self.state["phase"] = "destroying"
            self.save()
        for record in self.state.get("hosts", {}).values():
            try:
                self.recover_launch(record)
                if record.get("pid") and owned(record):
                    # pidfd prevents PID reuse between identity validation and signaling.
                    descriptor = os.pidfd_open(record["pid"])
                    try:
                        if not owned(record):
                            raise ValueError(
                                "Guest exited during ownership validation; retry destroy."
                            )
                        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                        deadline = time.monotonic() + timeout
                        while process(record["pid"]) and time.monotonic() < deadline:
                            time.sleep(0.1)
                        if process(record["pid"]):
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                            deadline = time.monotonic() + 5
                            while process(record["pid"]) and time.monotonic() < deadline:
                                time.sleep(0.1)
                        if process(record["pid"]):
                            raise ValueError("Guest has not exited; resources retained.")
                    finally:
                        os.close(descriptor)
                elif record["status"] == "launching" and (
                    Path(record["pidfile"]).exists() or Path(record["qmp"]).exists()
                ):
                    raise ValueError("Unverified interrupted launch; resources retained.")
                artifacts = [
                    record[k]
                    for k in (
                        "pidfile",
                        "qmp",
                        "overlay",
                        "seed_iso",
                        "cidata",
                        "ovmf_vars",
                    )
                ]
                artifacts += list(record["disks"].values())
                if not record["diagnostics"]["retain_logs"]:
                    artifacts += [record["serial_log"], record["stderr_log"]]
                for artifact in artifacts:
                    path = Path(artifact)
                    if path.parent != self.directory:
                        raise ValueError(
                            "Artifact path escapes the run directory; refusing cleanup."
                        )
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
                record["status"] = "removed"
                changed = True
                self.release_unused()
                self.save()
            except (OSError, ValueError) as error:
                errors.append(record["logical_name"] + ": " + str(error))
        if errors:
            raise ValueError("Cleanup incomplete; state retained for retry. " + "; ".join(errors))
        if self.state:
            inventory = self.directory / "inventory" / "molecule_runtime.yml"
            inventory.unlink(missing_ok=True)
            self.path.unlink(missing_ok=True)
            self.state = {}
            self.release_unused()
            atomic_write(self.reservations_path, self.reservations)
        return changed
