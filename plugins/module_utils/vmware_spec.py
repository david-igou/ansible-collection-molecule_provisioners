# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
"""Offline VMware input, ownership and connection contracts."""

import base64
import copy
import hashlib
import ipaddress
import json
import re
import shlex
import uuid

try:
    import yaml
except ImportError:
    yaml = None

SSH_ARGS = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes"
FIELDS = {
    "vcenter",
    "datacenter",
    "folder",
    "cluster",
    "esxi_hostname",
    "template",
    "snapshot_src",
    "linked_clone",
    "resource_pool",
    "datastore",
    "name",
    "hardware",
    "networks",
    "instance_os_type",
    "guest",
    "customization",
    "bootstrap",
    "ssh_key_path",
    "connection",
    "connection_address",
    "connection_port",
    "connection_vars",
}


def merge(base, override):
    """Recursively combine mappings, replacing lists rather than extending them."""
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        raise ValueError("VMware settings must be mappings")
    for key, value in override.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else copy.deepcopy(value)
        )
    return result


def positive(value, field):
    """Reject booleans and non-positive integer limits."""
    if type(value) is not int or value <= 0:
        raise ValueError("%s must be a positive integer" % field)
    return value


def normalize(hosts, defaults=None, role_defaults=None):
    """Validate every effective host before controller or vCenter mutation."""
    result = {}
    for host, value in hosts.items():
        spec = merge(merge(role_defaults or {}, defaults or {}), value)
        if set(spec) - FIELDS:
            raise ValueError("Unknown VMware fields: %s" % ", ".join(sorted(set(spec) - FIELDS)))
        for field in ("datacenter", "folder", "template"):
            if not isinstance(spec.get(field), str) or not spec[field].strip():
                raise ValueError("VMware %s is required" % field)
        if bool(spec.get("cluster")) == bool(spec.get("esxi_hostname")):
            raise ValueError("Specify exactly one VMware cluster or esxi_hostname")
        os_type = spec.setdefault("instance_os_type", "linux")
        if os_type not in ("linux", "windows"):
            raise ValueError("instance_os_type must be linux or windows")
        spec.setdefault("connection", "winrm" if os_type == "windows" else "ssh")
        if spec["connection"] != ("winrm" if os_type == "windows" else "ssh"):
            raise ValueError("Phase one supports Linux ssh and Windows winrm")
        spec.setdefault("connection_port", 5986 if os_type == "windows" else 22)
        if positive(spec["connection_port"], "connection_port") > 65535:
            raise ValueError("connection_port exceeds 65535")
        for field in (
            "vcenter",
            "guest",
            "hardware",
            "customization",
            "bootstrap",
            "connection_vars",
        ):
            if not isinstance(spec.setdefault(field, {}), dict):
                raise ValueError("%s must be a mapping" % field)
        vc = spec["vcenter"]
        if set(vc) - {"hostname", "port", "credentials", "validate_certs"}:
            raise ValueError("Unknown vcenter settings")
        if "validate_certs" in vc and type(vc["validate_certs"]) is not bool:
            raise ValueError("vcenter.validate_certs must be boolean")
        if "port" in vc and positive(vc["port"], "vcenter.port") > 65535:
            raise ValueError("vcenter.port exceeds 65535")
        for field in ("guest", "vcenter"):
            credentials = spec[field].setdefault("credentials", {})
            if not isinstance(credentials, dict) or set(credentials) - {"username", "password"}:
                raise ValueError("%s.credentials accepts username and password" % field)
        if set(spec["guest"]) != {"credentials"}:
            raise ValueError("guest accepts credentials only")
        credentials = spec["guest"]["credentials"]
        if not isinstance(credentials.get("username"), str) or not credentials["username"]:
            raise ValueError("guest.credentials.username is required")
        bootstrap = spec["bootstrap"]
        if set(bootstrap) - {"method"} or bootstrap.setdefault("method", "tools") not in (
            "tools",
            "prepared",
            "guestinfo",
        ):
            raise ValueError("bootstrap.method must be tools, prepared or guestinfo")
        if (bootstrap["method"] == "tools" or os_type == "windows") and not credentials.get(
            "password"
        ):
            raise ValueError("Tools bootstrap and Windows require guest.credentials.password")
        if (
            bootstrap["method"] == "prepared"
            and os_type == "linux"
            and not spec.get("ssh_key_path")
        ):
            raise ValueError("Prepared Linux requires ssh_key_path")
        if bootstrap["method"] == "guestinfo" and (
            os_type != "linux"
            or len(spec.get("networks", [])) != 1
            or spec["networks"][0].get("type", "dhcp") != "dhcp"
            or spec["networks"][0].get("ip")
        ):
            raise ValueError("GuestInfo bootstrap currently requires Linux with one DHCP NIC")
        custom = spec["customization"]
        allowed = {
            "hostname",
            "dns_servers",
            "fullname",
            "orgname",
            "autologon",
            "autologoncount",
            "password",
        }
        if set(custom) - allowed:
            raise ValueError("Unknown customization settings")
        custom.setdefault("hostname", host)
        if not isinstance(custom["hostname"], str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", custom["hostname"]
        ):
            raise ValueError("Set a valid customization.hostname for this inventory host")
        if os_type == "windows" and len(custom["hostname"]) > 15:
            raise ValueError("Windows customization.hostname must be at most 15 characters")
        if "password" in custom and custom["password"] != credentials.get("password"):
            raise ValueError("customization.password conflicts with guest credentials")
        custom.pop("password", None)
        if os_type == "windows":
            custom = merge(
                {
                    "fullname": "molecule",
                    "orgname": "molecule",
                    "autologon": False,
                    "autologoncount": 1,
                },
                custom,
            )
        spec["customization"] = custom
        spec.setdefault("linked_clone", bool(spec.get("snapshot_src")))
        if type(spec["linked_clone"]) is not bool or (
            spec["linked_clone"] and not spec.get("snapshot_src")
        ):
            raise ValueError("linked_clone must be boolean and requires snapshot_src when true")
        networks = spec.get("networks")
        if (
            not isinstance(networks, list)
            or not networks
            or not all(isinstance(n, dict) for n in networks)
        ):
            raise ValueError("networks must be a nonempty ordered list of mappings")
        for network in networks:
            network.setdefault("connected", True)
            network.setdefault("start_connected", True)
            if not network.get("name") and "vlan" not in network:
                raise ValueError("Each network requires name or vlan")
            if network.get("ip"):
                if not network.get("netmask") or network.get("type", "static") != "static":
                    raise ValueError("Static network requires netmask and type static")
                ipaddress.IPv4Interface(network["ip"] + "/" + network["netmask"])
                if network.get("gateway"):
                    ipaddress.IPv4Address(network["gateway"])
        if bootstrap["method"] == "guestinfo":
            network = networks[0]
            allowed_network = {
                "name",
                "type",
                "device_type",
                "dvswitch_name",
                "connected",
                "start_connected",
            }
            if (
                not network.get("name")
                or set(network) - allowed_network
                or not network["connected"]
                or not network["start_connected"]
            ):
                raise ValueError(
                    "GuestInfo requires a named, connected DHCP NIC with supported hardware settings"
                )
            if set(custom) - {"hostname"}:
                raise ValueError("GuestInfo uses DHCP DNS and customization.hostname only")
        reserved = {
            "ansible_host",
            "ansible_port",
            "ansible_user",
            "ansible_connection",
            "ansible_password",
            "ansible_private_key_file",
            "ansible_ssh_private_key_file",
        }
        if reserved.intersection(spec["connection_vars"]) or any(
            not k.startswith("ansible_") for k in spec["connection_vars"]
        ):
            raise ValueError(
                "Use explicit VMware connection fields for endpoint, user, password and key"
            )
        if spec.get("name") and (
            not isinstance(spec["name"], str) or "/" in spec["name"] or len(spec["name"]) > 80
        ):
            raise ValueError("VM name must be a string of at most 80 characters without slashes")
        result[host] = spec
    if not result:
        raise ValueError("At least one VMware host is required")
    if any(s["vcenter"] != next(iter(result.values()))["vcenter"] for s in result.values()):
        raise ValueError(
            "All VMware hosts must use one vCenter endpoint/authentication/TLS configuration"
        )
    names = [s["name"] for s in result.values() if s.get("name")]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate fixed VMware VM names")
    return result


def fingerprint(spec):
    """Save no credential or credential-derived fingerprint in the public journal."""
    public = copy.deepcopy(spec)
    public["vcenter"].pop("credentials", None)
    public["guest"]["credentials"].pop("password", None)
    return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()


def marker(run_id, host):
    """Bind cloud ownership to a run and logical host independently of VM name."""
    return "molecule_provisioners:%s:%s" % (run_id, hashlib.sha256(host.encode()).hexdigest()[:16])


def new_run(specs, plans, vcenter_uuid):
    """Build non-secret intents before the first clone submission."""
    run_id = uuid.uuid4().hex
    hosts = {}
    for host, spec in specs.items():
        slug = re.sub(r"[^a-zA-Z0-9-]", "-", host)[:32]
        name = spec.get("name") or "mp-%s-%s-%s" % (
            slug,
            hashlib.sha256(host.encode()).hexdigest()[:8],
            run_id[:12],
        )
        hosts[host] = {
            "name": name,
            "fingerprint": fingerprint(spec),
            "annotation": marker(run_id, host),
            "plan": plans[host],
            "phase": "planned",
        }
    return {
        "version": 1,
        "id": run_id,
        "vcenter_uuid": vcenter_uuid,
        "hosts": hosts,
        "generated_key": any(
            s["instance_os_type"] == "linux"
            and s["bootstrap"]["method"] in ("tools", "guestinfo")
            and not s.get("ssh_key_path")
            for s in specs.values()
        ),
    }


def validate_run(run):
    """Refuse malformed or mismatched ownership before any destructive operation."""
    if (
        not isinstance(run, dict)
        or run.get("version") != 1
        or not re.fullmatch(r"[a-f0-9]{32}", str(run.get("id", "")))
    ):
        raise ValueError("Restore a valid vmware_run.yml before retrying")
    if not isinstance(run.get("hosts"), dict) or not run.get("vcenter_uuid"):
        raise ValueError("Invalid VMware ownership journal")
    for host, record in run["hosts"].items():
        if (
            record.get("annotation") != marker(run["id"], host)
            or not record.get("name")
            or not isinstance(record.get("plan"), dict)
        ):
            raise ValueError("Invalid VMware host ownership record")
        for field in ("job_id", "delete_job_id"):
            if field in record and not re.fullmatch(r"j?[0-9]+\.[0-9]+", str(record[field])):
                raise ValueError("Invalid VMware worker identity in saved state")
    return run


def update_host(run, host, values):
    """Return a checkpoint without mutating input facts."""
    result = copy.deepcopy(validate_run(run))
    result["hosts"][host].update(values)
    return result


def request(spec, record, public_key="preflight"):
    """Forward the supported wrapper and nested vendor settings to vmware_guest."""
    result = {
        k: spec[k]
        for k in (
            "datacenter",
            "cluster",
            "esxi_hostname",
            "resource_pool",
            "datastore",
            "snapshot_src",
            "linked_clone",
            "hardware",
            "networks",
        )
        if k in spec
    }
    result.update(
        name=record["name"],
        folder=record["plan"]["folder"],
        template=record["plan"]["template"],
        annotation=record["annotation"],
        customization=copy.deepcopy(spec["customization"]),
        state="poweredon",
        wait_for_ip_address=True,
        wait_for_customization=True,
    )
    if spec["bootstrap"]["method"] == "guestinfo":
        if yaml is None:
            raise ValueError("GuestInfo bootstrap requires PyYAML on the controller")
        metadata = {
            "instance-id": record["annotation"],
            "local-hostname": spec["customization"]["hostname"],
            "network": {
                "version": 2,
                "ethernets": {"primary": {"match": {"name": "e*"}, "dhcp4": True, "dhcp6": False}},
            },
            "redact": ["userdata", "vendordata"],
        }
        data = {
            "ssh_pwauth": False,
            "users": [
                {
                    "name": spec["guest"]["credentials"]["username"],
                    "shell": "/bin/bash",
                    "lock_passwd": True,
                    "sudo": "ALL=(ALL) NOPASSWD:ALL",
                    "ssh_authorized_keys": [public_key],
                }
            ],
        }
        settings = {
            "guestinfo.metadata": base64.b64encode(json.dumps(metadata).encode()).decode(),
            "guestinfo.metadata.encoding": "base64",
            "guestinfo.userdata": base64.b64encode(
                (
                    "#cloud-config\n"
                    + yaml.safe_dump(json.loads(json.dumps(data)), default_flow_style=False)
                ).encode()
            ).decode(),
            "guestinfo.userdata.encoding": "base64",
        }
        result.pop("customization")
        # vmware_guest infers OS customization even from a DHCP network list.
        # Clone powered off without that list; the role configures the existing
        # NIC through vmware_guest_network before the first power-on instead.
        result["networks"] = []
        result["state"] = "present"
        result["wait_for_ip_address"] = False
        result["wait_for_customization"] = False
        result["advanced_settings"] = [
            {"key": key, "value": value} for key, value in settings.items()
        ]
    if spec["instance_os_type"] == "windows":
        result["customization"]["password"] = spec["guest"]["credentials"]["password"]
    return result


def runtime(spec, observation, key_path):
    """Select management IPv4 from the first NIC and build a private native overlay."""
    address = spec.get("connection_address")
    if not address:
        candidates = [
            a
            for a in observation.get("management_ips", [])
            if isinstance(ipaddress.ip_address(a), ipaddress.IPv4Address)
            and not ipaddress.ip_address(a).is_loopback
            and not ipaddress.ip_address(a).is_link_local
        ]
        static = spec["networks"][0].get("ip")
        if static:
            if static not in candidates:
                raise ValueError("Configured management IP is not reported by the first NIC")
            address = static
        elif len(candidates) == 1:
            address = candidates[0]
        else:
            raise ValueError("Set connection_address: no unique management NIC IPv4")
    values = {
        "ansible_host": address,
        "ansible_port": spec["connection_port"],
        "ansible_user": spec["guest"]["credentials"]["username"],
        "ansible_connection": spec["connection"],
        "mp_vmware_instance_uuid": observation["instance_uuid"],
        "mp_vmware_moid": observation["moid"],
        "mp_vmware_name": observation["name"],
        "mp_vmware_bootstrap": spec["bootstrap"]["method"],
    }
    if spec["connection"] == "ssh":
        values.update(ansible_ssh_private_key_file=key_path, ansible_ssh_common_args=SSH_ARGS)
    else:
        values.update(
            ansible_password=spec["guest"]["credentials"]["password"],
            ansible_winrm_transport="ntlm",
            ansible_winrm_scheme="https",
            ansible_winrm_server_cert_validation="ignore",
        )
    return merge(values, spec["connection_vars"])


def linux_command(public_key):
    """Install only a public key; preserve existing access and quote shell data."""
    if not public_key.strip().startswith(("ssh-", "ecdsa-")) or "\n" in public_key.strip():
        raise ValueError("Invalid SSH public key")
    script = (
        'set -eu; umask 077; mkdir -p "$HOME/.ssh"; touch "$HOME/.ssh/authorized_keys"; '
        'chmod 700 "$HOME/.ssh"; chmod 600 "$HOME/.ssh/authorized_keys"; key=%s; '
        'grep -qxF -- "$key" "$HOME/.ssh/authorized_keys" || '
        'printf "%%s\\n" "$key" >> "$HOME/.ssh/authorized_keys"' % shlex.quote(public_key.strip())
    )
    return "-c " + shlex.quote(script)


def remove_host(run, host):
    """Forget only a successfully deleted owned resource."""
    result = copy.deepcopy(validate_run(run))
    result["hosts"].pop(host)
    return result


def discovery_specs(specs):
    """Send only non-secret inventory selectors to the read-only SDK helper."""
    fields = {
        "datacenter",
        "folder",
        "template",
        "cluster",
        "esxi_hostname",
        "resource_pool",
        "datastore",
        "networks",
        "snapshot_src",
        "bootstrap",
    }
    return {
        host: {key: value for key, value in spec.items() if key in fields}
        for host, spec in specs.items()
    }
