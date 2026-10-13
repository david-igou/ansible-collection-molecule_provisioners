# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT
"""Validate QEMU inputs and translate them into role-owned launch arguments."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from copy import deepcopy

RESERVED = {"-name", "-uuid", "-pidfile", "-qmp", "-daemonize", "-readconfig"}


def require(condition: object, message: str) -> None:
    """Reject invalid inputs before provisioning."""
    if not condition:
        raise ValueError(message)


def fields(value: dict, allowed: set[str], label: str) -> None:
    """Reject misspelled structured inputs rather than persisting unused fields."""
    require(not set(value) - allowed, "Unknown fields in " + label + ".")


def port(value: object, automatic: bool = False) -> None:
    """Validate a guest or controller port."""
    require(
        (automatic and value == "auto")
        or (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535),
        "Ports must be integers in 1..65535 (or auto for host ports).",
    )


def name(value: object) -> None:
    """Require names safe for paths and QEMU identifiers."""
    require(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value),
        "Names must contain 1..64 letters, digits, dots, underscores or hyphens.",
    )


def normalize(specs: dict) -> dict:
    """Validate merged host specs and set dependent defaults."""
    result = deepcopy(specs)
    definitions = {}
    members = {}
    for host, spec in result.items():
        name(host)
        require(isinstance(spec, dict), "QEMU host specs must be mappings.")
        require(
            isinstance(spec.get("image"), str) and bool(spec["image"]),
            f"Host '{host}' is missing qemu.image.",
        )
        for field in ("memory", "cpus"):
            require(
                isinstance(spec.get(field), int)
                and not isinstance(spec.get(field), bool)
                and spec[field] > 0,
                field + " must be a positive integer.",
            )
        require(
            spec.get("firmware", "bios") in ("bios", "uefi"),
            "firmware must be bios or uefi.",
        )
        require(
            isinstance(spec.get("ssh_user"), str) and spec["ssh_user"],
            "ssh_user must be a nonempty string.",
        )
        require(isinstance(spec.get("cpu_model", ""), str), "cpu_model must be a string.")
        spec.setdefault("ssh_port", 22)
        spec.setdefault("ssh_host_port", "auto")
        port(spec["ssh_port"])
        port(spec["ssh_host_port"], True)
        spec.setdefault("connection", "ssh")
        require(
            spec["connection"] in ("ssh", "network_cli"),
            "connection must be ssh or network_cli.",
        )
        connection_vars = spec.setdefault("connection_vars", {})
        require(isinstance(connection_vars, dict), "connection_vars must be a mapping.")
        require(
            all(isinstance(k, str) and k.startswith("ansible_") for k in connection_vars),
            "connection_vars keys must be Ansible connection variables.",
        )
        require(
            not set(connection_vars) & {"ansible_host", "ansible_port", "ansible_connection"},
            "connection_vars cannot override managed host, port or connection.",
        )
        if spec["connection"] == "network_cli":
            require(
                isinstance(spec.get("connection_probe"), str) and spec["connection_probe"],
                "network_cli requires a read-only connection_probe command.",
            )
            require(
                bool(connection_vars.get("ansible_network_os")),
                "network_cli requires ansible_network_os.",
            )
        cloud = spec.setdefault("cloud_init", {})
        require(isinstance(cloud, dict), "cloud_init must be a mapping.")
        fields(
            cloud,
            {
                "enabled",
                "inject_ssh_key",
                "mode",
                "user_data",
                "metadata",
                "network_data",
            },
            "cloud_init",
        )
        cloud.setdefault("enabled", spec["connection"] == "ssh")
        cloud.setdefault("inject_ssh_key", cloud["enabled"] and spec["connection"] == "ssh")
        cloud.setdefault("mode", "merge")
        require(
            isinstance(cloud["enabled"], bool) and isinstance(cloud["inject_ssh_key"], bool),
            "Cloud-init switches must be booleans.",
        )
        require(
            cloud["enabled"] or not cloud["inject_ssh_key"],
            "Disabled cloud-init cannot inject an SSH key.",
        )
        require(
            cloud["mode"] in ("merge", "replace"),
            "cloud_init.mode must be merge or replace.",
        )
        for field in ("user_data", "metadata", "network_data"):
            require(
                isinstance(cloud.get(field, {}), dict),
                "cloud_init." + field + " must be a mapping.",
            )
        require(
            "instance-id" not in cloud.get("metadata", {}),
            "Cloud-init instance-id is managed by the provisioner.",
        )
        require(
            not cloud.get("user_data", {}).get("users")
            or isinstance(cloud["user_data"]["users"], list),
            "Cloud-init users must be a list.",
        )
        diag = spec.setdefault("diagnostics", {})
        require(isinstance(diag, dict), "diagnostics must be a mapping.")
        fields(diag, {"serial_log", "retain_logs", "failure_tail_lines"}, "diagnostics")
        diag.setdefault("serial_log", True)
        diag.setdefault("retain_logs", True)
        diag.setdefault("failure_tail_lines", 100)
        require(
            isinstance(diag["serial_log"], bool) and isinstance(diag["retain_logs"], bool),
            "Diagnostic switches must be booleans.",
        )
        require(
            isinstance(diag["failure_tail_lines"], int)
            and not isinstance(diag["failure_tail_lines"], bool)
            and 1 <= diag["failure_tail_lines"] <= 1000,
            "failure_tail_lines must be in 1..1000.",
        )
        disks = spec.setdefault("data_disks", [])
        require(isinstance(disks, list), "data_disks must be a list.")
        disk_names = set()
        for disk in disks:
            require(isinstance(disk, dict), "Disk entries must be mappings.")
            fields(
                disk,
                {
                    "name",
                    "size",
                    "image",
                    "image_checksum",
                    "bus",
                    "serial",
                    "read_only",
                },
                "data_disks",
            )
            name(disk.get("name"))
            require(disk["name"] not in disk_names, "Duplicate disk name.")
            disk_names.add(disk["name"])
            require(disk.get("size") or disk.get("image"), "Blank data disks require size.")
            disk.setdefault("bus", "virtio")
            disk.setdefault("read_only", False)
            require(
                disk["bus"] in ("virtio", "scsi", "sata"),
                "Disk bus must be virtio, scsi or sata.",
            )
            require(isinstance(disk["read_only"], bool), "Disk read_only must be a boolean.")
            if disk.get("image"):
                require(
                    isinstance(disk["image"], str),
                    "Disk image must be a URL or file URL.",
                )
            if "serial" in disk:
                require(
                    isinstance(disk["serial"], str)
                    and re.fullmatch(r"[A-Za-z0-9_.-]+", disk["serial"]),
                    "Disk serial must contain letters, digits, dots, underscores or hyphens.",
                )
        for disk in [spec] + disks:
            require(
                "image_checksum" not in disk or isinstance(disk["image_checksum"], str),
                "image_checksum must be a string.",
            )
            size = disk.get("disk_size", disk.get("size", ""))
            require(
                not size or (isinstance(size, str) and re.fullmatch(r"[1-9][0-9]*[KMGT]?", size)),
                "Disk sizes must be positive absolute sizes such as 2G; relative resizing is unsupported.",
            )
        require(
            sum(disk["bus"] == "sata" for disk in disks) <= 6,
            "The managed SATA controller supports at most six disks.",
        )
        forwards = spec.setdefault("application_ports", [])
        require(isinstance(forwards, list), "application_ports must be a list.")
        forward_names = set()
        for forward in forwards:
            require(isinstance(forward, dict), "Application ports must be mappings.")
            fields(
                forward,
                {"name", "protocol", "port", "host_port", "bind_address"},
                "application_ports",
            )
            name(forward.get("name"))
            require(forward["name"] not in forward_names, "Duplicate application port name.")
            forward_names.add(forward["name"])
            forward.setdefault("protocol", "TCP")
            forward.setdefault("host_port", "auto")
            forward.setdefault("bind_address", "127.0.0.1")
            require(
                forward["protocol"] in ("TCP", "UDP"),
                "Application protocol must be TCP or UDP.",
            )
            require(
                isinstance(forward["bind_address"], str),
                "Application bind_address must be an IPv4 address.",
            )
            try:
                ipaddress.IPv4Address(forward["bind_address"])
            except ipaddress.AddressValueError:
                raise ValueError("Application bind_address must be an IPv4 address.") from None
            port(forward.get("port"))
            port(forward["host_port"], True)
        networks = spec.setdefault("networks", [])
        nics = spec.setdefault("nics", [])
        require(
            isinstance(networks, list) and isinstance(nics, list),
            "networks and nics must be lists.",
        )
        local_networks = {}
        for network in networks:
            require(isinstance(network, dict), "Network entries must be mappings.")
            name(network.get("name"))
            require(network["name"] not in local_networks, "Duplicate network name.")
            require(
                network.get("type") in ("user", "socket"),
                "Network type must be user or socket.",
            )
            require(set(network) <= {"name", "type"}, "Networks accept name and type only.")
            require(
                network["name"] not in definitions or definitions[network["name"]] == network,
                "Shared network definitions must agree.",
            )
            definitions[network["name"]] = network
            local_networks[network["name"]] = network
        nic_names = set()
        for nic in nics:
            require(isinstance(nic, dict), "NIC entries must be mappings.")
            fields(nic, {"name", "network", "model", "mac"}, "nics")
            name(nic.get("name"))
            require(
                nic["name"] not in nic_names and nic["name"] != "net0",
                "Duplicate or reserved NIC name.",
            )
            nic_names.add(nic["name"])
            require(
                nic.get("network") in local_networks,
                "NIC references an undefined network.",
            )
            nic.setdefault("model", "virtio-net-pci")
            require(
                isinstance(nic["model"], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", nic["model"]),
                "Invalid NIC model.",
            )
            if "mac" in nic:
                require(
                    isinstance(nic["mac"], str)
                    and re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", nic["mac"]),
                    "Invalid NIC MAC address.",
                )
            members.setdefault(nic["network"], []).append(host)
        args = spec.setdefault("extra_args", [])
        require(
            isinstance(args, list) and all(isinstance(arg, str) for arg in args),
            "extra_args must be a list of strings.",
        )
        for arg in args:
            option = "-" + arg.lstrip("-").split("=")[0] if arg.startswith("-") else arg
            require(
                option not in RESERVED,
                "extra_args cannot override managed process identity.",
            )
            require(
                not re.search(r"(?:^|,)id=(?:net0|nic-management|mp-[^,]*)($|,)", arg),
                "extra_args cannot reuse managed network or device IDs.",
            )
            require("hostfwd=" not in arg, "Use application_ports for host forwards.")
            require(
                arg != "-serial" or not diag["serial_log"],
                "Disable managed serial capture before supplying -serial.",
            )
    macs = [nic["mac"].lower() for spec in result.values() for nic in spec["nics"] if "mac" in nic]
    require(len(macs) == len(set(macs)), "NIC MAC addresses must be unique within a run.")
    for network, definition in definitions.items():
        if definition["type"] == "socket":
            peers = members.get(network, [])
            require(
                len(peers) == 2 and len(set(peers)) == 2,
                "Socket networks require exactly two distinct guest peers.",
            )
    return result


def valid(specs: dict) -> bool:
    """Validate without returning secret-bearing specifications."""
    normalize(specs)
    return True


def size_bytes(value: str) -> int:
    """Convert the supported absolute disk size syntax to bytes."""
    if not value:
        return 0
    multiplier = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    return int(value[:-1]) * multiplier[value[-1]] if value[-1] in multiplier else int(value)


def fingerprint(spec: dict, boot_options: dict | None = None) -> str:
    """Hash boot configuration without persisting bootstrap data or credentials."""
    boot = {
        key: value
        for key, value in spec.items()
        if key
        not in (
            "connection_vars",
            "connection_probe",
            "diagnostics",
            "ssh_host",
            "base_image_path",
            "overlay_path",
            "seed_iso_path",
        )
    }
    boot["serial_log"] = spec["diagnostics"]["serial_log"]
    options = boot_options or {}
    if spec["cloud_init"]["inject_ssh_key"]:
        boot["ssh_key_path"] = options.get("ssh_key_path", "")
    if spec["firmware"] == "uefi":
        boot["firmware_files"] = [
            options.get("ovmf_code", ""),
            options.get("ovmf_vars", ""),
        ]
    return hashlib.sha256(json.dumps(boot, sort_keys=True).encode()).hexdigest()


def requests(specs: dict, boot_options: dict | None = None) -> dict:
    """Return only non-secret resource allocation requests."""
    return {
        host: {
            "fingerprint": fingerprint(spec, boot_options),
            "ssh_host_port": spec["ssh_host_port"],
            "ssh_port": spec["ssh_port"],
            "application_ports": spec["application_ports"],
            "data_disks": [disk["name"] for disk in spec["data_disks"]],
            "networks": spec["networks"],
            "nics": spec["nics"],
            "diagnostics": spec["diagnostics"],
        }
        for host, spec in specs.items()
    }


def merge(left: dict, right: dict) -> dict:
    """Recursively merge mappings while replacing lists."""
    result = deepcopy(left)
    for key, value in right.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else deepcopy(value)
        )
    return result


def user_data(spec: dict, public_key: str) -> dict:
    """Merge bootstrap defaults and preserve caller-defined users and keys."""
    cloud = spec["cloud_init"]
    defaults = {"chpasswd": {"expire": False}}
    if spec.get("disk_size"):
        defaults.update(growpart={"mode": "auto", "devices": ["/"]}, resize_rootfs=True)
    result = (
        merge(defaults, cloud.get("user_data", {}))
        if cloud["mode"] == "merge"
        else deepcopy(cloud.get("user_data", {}))
    )
    if cloud["inject_ssh_key"]:
        users = result.setdefault("users", [])
        target = next(
            (
                user
                for user in users
                if isinstance(user, dict) and user.get("name") == spec["ssh_user"]
            ),
            None,
        )
        if target is None:
            target = {
                "name": spec["ssh_user"],
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "shell": "/bin/bash",
            }
            users.append(target)
        keys = target.setdefault("ssh_authorized_keys", [])
        require(isinstance(keys, list), "ssh_authorized_keys must be a list.")
        if public_key not in keys:
            keys.append(public_key)
    return result


def stable_mac(runtime: dict, label: str) -> str:
    """Derive a locally administered address from owned VM identity."""
    digest = hashlib.sha256((runtime["vm_uuid"] + label).encode()).hexdigest()[:10]
    return "02:" + ":".join(digest[i : i + 2] for i in range(0, 10, 2))


def network_data(spec: dict, runtime: dict) -> dict:
    """Keep management DHCP when configuring version-two extra interfaces."""
    supplied = spec["cloud_init"].get("network_data", {})
    if supplied.get("version") != 2:
        return deepcopy(supplied)
    defaults = {
        "version": 2,
        "ethernets": {
            "mp-mgmt": {
                "match": {"macaddress": stable_mac(runtime, "management")},
                "dhcp4": True,
            }
        },
    }
    return merge(defaults, supplied)


def launch_argv(spec: dict, runtime: dict, kvm: bool, ovmf_code: str) -> list[str]:
    """Translate the validated role specification into QEMU argv."""
    argv = ["qemu-system-x86_64"]
    if spec["firmware"] == "uefi":
        argv += [
            "-drive",
            "if=pflash,format=raw,readonly=on,file=" + ovmf_code,
            "-drive",
            "if=pflash,format=raw,file=" + runtime["ovmf_vars"],
        ]
    cpu = spec.get("cpu_model") or ("host" if kvm else "Nehalem")
    argv += [
        "-machine",
        "accel=" + ("kvm" if kvm else "tcg"),
        "-cpu",
        cpu,
        "-m",
        str(spec["memory"]),
        "-smp",
        str(spec["cpus"]),
        "-drive",
        "file={},if=none,id=mp-root,format=qcow2".format(runtime["overlay"]),
        "-device",
        "virtio-blk-pci,drive=mp-root,id=mp-root-device,bootindex=1",
        "-name",
        runtime["physical_name"],
        "-uuid",
        runtime["vm_uuid"],
        "-daemonize",
        "-pidfile",
        runtime["pidfile"],
        "-qmp",
        "unix:{},server=on,wait=off".format(runtime["qmp"]),
        "-display",
        "none",
    ]
    if spec["cloud_init"]["enabled"]:
        if spec["firmware"] == "uefi":
            argv += [
                "-drive",
                "file=" + runtime["seed_iso"] + ",format=raw,if=none,id=mp-seed,readonly=on",
                "-device",
                "virtio-blk-pci,drive=mp-seed,id=mp-seed-device",
            ]
        else:
            argv += ["-cdrom", runtime["seed_iso"]]
    if spec["diagnostics"]["serial_log"]:
        argv += [
            "-chardev",
            "file,id=serial0,path=" + runtime["serial_log"],
            "-serial",
            "chardev:serial0",
        ]
    net = "user,id=net0,hostfwd=tcp:127.0.0.1:{}-:{}".format(runtime["ssh_port"], spec["ssh_port"])
    for endpoint in runtime["endpoints"].values():
        net += ",hostfwd={}:{}:{}-:{}".format(
            endpoint["protocol"].lower(),
            endpoint["host"],
            endpoint["port"],
            endpoint["guest_port"],
        )
    argv += [
        "-netdev",
        net,
        "-device",
        "virtio-net-pci,netdev=net0,id=nic-management,mac=" + stable_mac(runtime, "management"),
    ]
    buses = {disk["bus"] for disk in spec["data_disks"]}
    if "scsi" in buses:
        argv += ["-device", "virtio-scsi-pci,id=mp-scsi"]
    if "sata" in buses:
        argv += ["-device", "ich9-ahci,id=mp-sata"]
    sata_index = 0
    for disk in spec["data_disks"]:
        disk_id = "mp-disk-" + disk["name"]
        drive = "file={},if=none,id={},format=qcow2,readonly={}".format(
            runtime["disks"][disk["name"]],
            disk_id,
            "on" if disk["read_only"] else "off",
        )
        device = {
            "virtio": "virtio-blk-pci",
            "scsi": "scsi-hd,bus=mp-scsi.0",
            "sata": f"ide-hd,bus=mp-sata.{sata_index}",
        }[disk["bus"]]
        device += ",drive=" + disk_id
        if "serial" in disk:
            device += ",serial=" + disk["serial"]
        argv += ["-drive", drive, "-device", device]
        if disk["bus"] == "sata":
            sata_index += 1
    for nic in spec["nics"]:
        nic_id = "mp-nic-" + nic["name"]
        link = runtime["networks"][nic["network"]]
        netdev = "user,id=" + nic_id
        if link["type"] == "socket":
            mode = "listen" if link["listener"] == runtime["logical_name"] else "connect"
            netdev = "socket,id={},{}=127.0.0.1:{}".format(nic_id, mode, link["port"])
        mac = nic.get("mac", stable_mac(runtime, "nic:" + nic["name"]))
        device = nic["model"] + ",netdev=" + nic_id + ",id=" + nic_id + ",mac=" + mac
        argv += ["-netdev", netdev, "-device", device]
    return argv + spec["extra_args"]
