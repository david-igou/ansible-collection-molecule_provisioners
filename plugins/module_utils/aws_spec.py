# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
"""Validate inventory and render EC2 launch requests without cloud mutations."""

import copy
import hashlib
import ipaddress
import json
import re

OWNER = "molecule-provisioners:managed-by"
RUN = "molecule-provisioners:run-id"
HOST = "molecule-provisioners:host"
MANAGER = "david_igou.molecule_provisioners"
DEFAULTS = {
    "instance_type": "t3.medium",
    "ssh_user": "cloud-user",
    "connection": "ssh",
    "connection_vars": {},
    "assign_public_ip": True,
    "connection_address": "public",
    "management_cidrs": [],
    "application_ports": [],
    "tags": {},
    "network": {"mode": "auto", "vpc_cidr": "10.77.0.0/24"},
}
FIELDS = set(DEFAULTS) | {
    "region",
    "profile",
    "expected_account_id",
    "image_id",
    "image_name",
    "image_owner",
    "image_filters",
    "subnet_id",
    "subnet_filters",
    "vpc_id",
    "vpc_filters",
    "security_groups",
    "security_group_name",
    "security_group_description",
    "security_group_rules",
    "security_group_rules_egress",
    "key_inject_method",
    "private_key_path",
    "key_name",
    "cloud_init",
    "user_data",
    "root_disk",
    "volumes",
    "existing_volumes",
    "network_interfaces",
    "network_interfaces_ids",
    "iam_instance_profile",
    "instance_options",
    "launch_template",
    "ssh_port",
    "connection_ip",
    "admin_user",
    "connection_probe",
    "boot_wait_seconds",
}
OPTION_NAMES = {
    "placement",
    "cpu_options",
    "cpu_credit_specification",
    "ebs_optimized",
    "detailed_monitoring",
    "metadata_options",
    "instance_initiated_shutdown_behavior",
}


def merge(left, right):
    """Recursively merge mappings while replacing lists and scalar values."""
    result = copy.deepcopy(left)
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _unknown(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(label + " must be a mapping")
    extra = set(value) - set(allowed)
    if extra:
        raise ValueError(label + " has unsupported fields: " + ", ".join(sorted(extra)))


def _integer(value, low, high, label):
    if (not isinstance(value, int) or isinstance(value, bool)) or not low <= value <= high:
        raise ValueError(label + " must be an integer in the documented range")


def normalize(hosts, shared=None):
    """Resolve and validate all hosts before the first infrastructure mutation."""
    if not isinstance(hosts, dict) or not hosts:
        raise ValueError("AWS requires at least one inventory host")
    result = {}
    for name, supplied in hosts.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
            raise ValueError("AWS inventory host names must contain 1-64 safe ASCII characters")
        raw = merge(shared or {}, supplied)
        _unknown(raw, FIELDS, "mp.aws")
        spec = merge(DEFAULTS, raw)
        windows = spec["connection"] in ("winrm", "psrp")
        if spec["connection"] not in ("ssh", "winrm", "psrp", "network_cli"):
            raise ValueError("AWS connection must be ssh, winrm, psrp or network_cli")
        spec.setdefault("ssh_port", 5986 if windows else 22)
        spec.setdefault(
            "key_inject_method",
            "ec2" if windows else "none" if spec["connection"] == "network_cli" else "cloud-init",
        )
        spec.setdefault("boot_wait_seconds", 0)
        spec.setdefault("admin_user", "Administrator")
        if spec["key_inject_method"] not in ("cloud-init", "ec2", "none"):
            raise ValueError("key_inject_method must be cloud-init, ec2 or none")
        _integer(spec["ssh_port"], 1, 65535, "ssh_port")
        _integer(spec["boot_wait_seconds"], 0, 3600, "boot_wait_seconds")
        if not isinstance(spec["instance_type"], str) or not spec["instance_type"]:
            raise ValueError("instance_type must be a nonempty string")
        for field in ("region", "profile", "ssh_user", "admin_user", "connection_ip"):
            if field in spec and (not isinstance(spec[field], str) or not spec[field]):
                raise ValueError(field + " must be a nonempty string when supplied")
        if not isinstance(spec["assign_public_ip"], bool):
            raise ValueError("assign_public_ip must be a boolean")
        if spec["connection_address"] not in ("public", "private"):
            raise ValueError("connection_address must be public or private")
        if (
            spec["connection_address"] == "public"
            and not spec["assign_public_ip"]
            and not spec.get("connection_ip")
        ):
            raise ValueError("Public management requires assign_public_ip or connection_ip")
        _unknown(
            spec["network"], ("mode", "vpc_cidr", "subnet_cidr", "availability_zone"), "network"
        )
        network = spec["network"]
        if network["mode"] not in ("auto", "create", "existing"):
            raise ValueError("network.mode must be auto, create or existing")
        references = any(
            spec.get(key)
            for key in (
                "subnet_id",
                "subnet_filters",
                "vpc_id",
                "vpc_filters",
                "security_groups",
                "network_interfaces_ids",
            )
        ) or any(nic.get("subnet_id") for nic in spec.get("network_interfaces", []))
        if references and network["mode"] == "create":
            raise ValueError("network.mode=create conflicts with existing network references")
        network["mode"] = (
            ("existing" if references else "create")
            if network["mode"] == "auto"
            else network["mode"]
        )
        network.setdefault("subnet_cidr", network["vpc_cidr"])
        if network["mode"] == "create":
            vpc = ipaddress.ip_network(network["vpc_cidr"], strict=True)
            subnet = ipaddress.ip_network(network["subnet_cidr"], strict=True)
            if (
                vpc.version != 4
                or not 16 <= vpc.prefixlen <= 28
                or not 16 <= subnet.prefixlen <= 28
                or not subnet.subnet_of(vpc)
            ):
                raise ValueError(
                    "Managed subnet must be a valid AWS IPv4 network contained in its VPC"
                )
        for field in (
            "tags",
            "connection_vars",
            "instance_options",
            "image_filters",
            "subnet_filters",
            "vpc_filters",
        ):
            if not isinstance(spec.get(field, {}), dict):
                raise ValueError(field + " must be a mapping")
        if any(key.startswith("molecule-provisioners:") or key == "Name" for key in spec["tags"]):
            raise ValueError("Ownership and Name tags are reserved")
        if set(spec["connection_vars"]) & {"ansible_host", "ansible_port", "ansible_connection"}:
            raise ValueError(
                "Use explicit connection/endpoint fields instead of overriding identity"
            )
        if any(not key.startswith("ansible_") for key in spec["connection_vars"]):
            raise ValueError("connection_vars accepts only ansible_* settings")
        _unknown(spec.get("instance_options", {}), OPTION_NAMES, "instance_options")
        if spec.get("image_id") and any(
            spec.get(key) for key in ("image_name", "image_owner", "image_filters")
        ):
            raise ValueError("image_id conflicts with AMI discovery selectors")
        if not spec.get("launch_template") and not any(
            spec.get(key) for key in ("image_id", "image_name", "image_filters")
        ):
            raise ValueError("Supply image_id, AMI discovery selectors or a pinned launch_template")
        if spec.get("launch_template"):
            template = spec["launch_template"]
            _unknown(template, ("id", "name", "version"), "launch_template")
            if (
                bool(template.get("id")) == bool(template.get("name"))
                or not str(template.get("version", "")).isdigit()
            ):
                raise ValueError(
                    "launch_template requires one ID/name and an exact numeric version"
                )
            spec["_explicit"] = sorted(raw)
            spec["_network_input_mode"] = raw.get("network", {}).get("mode", "auto")
        for field in (
            "volumes",
            "existing_volumes",
            "network_interfaces",
            "network_interfaces_ids",
            "application_ports",
            "security_groups",
            "management_cidrs",
        ):
            if not isinstance(spec.get(field, []), list):
                raise ValueError(field + " must be a list")
        if "user_data" in spec and not isinstance(spec["user_data"], str):
            raise ValueError("user_data must be an opaque string")
        indexes = []
        for index, nic in enumerate(
            spec.get("network_interfaces", []) or spec.get("network_interfaces_ids", [])
        ):
            if not isinstance(nic, dict):
                raise ValueError("Network interfaces must be mappings")
            _unknown(
                nic,
                (
                    ("id", "device_index")
                    if spec.get("network_interfaces_ids")
                    else (
                        "device_index",
                        "subnet_id",
                        "groups",
                        "private_ip_address",
                        "description",
                        "secondary_private_ip_address_count",
                        "private_ip_addresses",
                        "ipv6_addresses",
                        "ipv6_address_count",
                    )
                ),
                "network interface",
            )
            device_index = nic.get("device_index", index)
            _integer(device_index, 0, 31, "network device_index")
            if device_index in indexes:
                raise ValueError("Network device indexes must be unique")
            indexes.append(device_index)
            if "private_ip_address" in nic:
                if not isinstance(nic["private_ip_address"], str):
                    raise ValueError("Private IP addresses must be strings")
                ipaddress.IPv4Address(nic["private_ip_address"])
            addresses = nic.get("private_ip_addresses", [])
            if not isinstance(addresses, list):
                raise ValueError("private_ip_addresses must be a list")
            for address in addresses:
                _unknown(address, ("private_ip_address", "primary"), "private IP entry")
                if not isinstance(address.get("private_ip_address"), str):
                    raise ValueError("Private IP addresses must be strings")
                ipaddress.IPv4Address(address["private_ip_address"])
                if "primary" in address and not isinstance(address["primary"], bool):
                    raise ValueError("Private IP primary must be a boolean")
            if sum(address.get("primary", False) for address in addresses) > 1:
                raise ValueError("An interface can have only one primary private IP")
            if addresses and (
                nic.get("private_ip_address") or nic.get("secondary_private_ip_address_count")
            ):
                raise ValueError("Private IP list conflicts with address/count settings")
            if "groups" in nic and (
                not isinstance(nic["groups"], list)
                or any(not isinstance(group, str) for group in nic["groups"])
            ):
                raise ValueError("Interface groups must be a list of security group IDs")
            if (
                spec["assign_public_ip"]
                and device_index > 0
                and (nic.get("ipv6_addresses") or nic.get("ipv6_address_count"))
            ):
                raise ValueError("Secondary IPv6 interfaces require private-address launch mode")
        if indexes and 0 not in indexes:
            raise ValueError("Network interfaces require primary device index 0")
        if spec.get("network_interfaces_ids") and spec["assign_public_ip"]:
            raise ValueError(
                "Existing ENIs require assign_public_ip=false and a reachable private or explicit address"
            )
        if spec.get("network_interfaces") and spec.get("network_interfaces_ids"):
            raise ValueError("network_interfaces and network_interfaces_ids are mutually exclusive")
        if windows and spec["key_inject_method"] == "cloud-init":
            raise ValueError("Windows requires ec2 or none key injection")
        cloud = merge(
            {
                "enabled": not windows
                and spec["connection"] != "network_cli"
                and not spec.get("user_data"),
                "mode": "merge",
                "user_data": {},
            },
            spec.get("cloud_init", {}),
        )
        _unknown(cloud, ("enabled", "mode", "user_data"), "cloud_init")
        if not isinstance(cloud["enabled"], bool) or cloud["mode"] not in ("merge", "replace"):
            raise ValueError("cloud_init requires a boolean enabled and merge/replace mode")
        if not isinstance(cloud["user_data"], (dict, str)):
            raise ValueError("cloud_init.user_data must be a mapping or string")
        if isinstance(cloud["user_data"], str) and spec["key_inject_method"] == "cloud-init":
            raise ValueError("Opaque user data requires ec2 or none key injection")
        if spec.get("user_data") and (
            cloud["enabled"] or cloud["user_data"] or spec["key_inject_method"] == "cloud-init"
        ):
            raise ValueError(
                "Opaque user_data conflicts with cloud_init or cloud-init key injection"
            )
        if not cloud["enabled"] and spec["key_inject_method"] == "cloud-init":
            raise ValueError("Disabled cloud_init requires ec2 or none key injection")
        spec["cloud_init"] = cloud
        if spec["connection"] == "network_cli" and (
            not spec.get("connection_probe")
            or not spec["connection_vars"].get("ansible_network_os")
        ):
            raise ValueError("network_cli requires connection_probe and ansible_network_os")
        ports = set()
        for port in spec["application_ports"]:
            _unknown(port, ("name", "port", "protocol", "cidrs"), "application_ports entry")
            if (
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,62}", port.get("name", ""))
                or port["name"] in ports
            ):
                raise ValueError("Application port names must be safe and unique")
            ports.add(port["name"])
            _integer(port.get("port"), 1, 65535, "application port")
            port.setdefault("protocol", "TCP")
            if port["protocol"] not in ("TCP", "UDP"):
                raise ValueError("Application protocol must be TCP or UDP")
            for cidr in port.get("cidrs", []):
                ipaddress.ip_network(cidr, strict=False)
        for cidr in spec["management_cidrs"]:
            ipaddress.ip_network(cidr, strict=False)
        if (
            not spec.get("security_groups")
            and not spec.get("network_interfaces_ids")
            and not spec["management_cidrs"]
            and not spec.get("security_group_rules")
        ):
            raise ValueError(
                "Managed security groups require explicit management_cidrs or ingress rules"
            )
        root = spec.get("root_disk", {})
        _unknown(
            root,
            ("size", "volume_type", "encrypted", "kms_key_id", "iops", "throughput"),
            "root_disk",
        )
        if "size" in root:
            _integer(root["size"], 1, 65536, "root_disk.size")
        devices = set()
        for disk in spec.get("volumes", []):
            if (
                not isinstance(disk, dict)
                or not disk.get("device_name")
                or disk["device_name"] in devices
            ):
                raise ValueError("volumes require unique device_name values")
            devices.add(disk["device_name"])
            if disk.get("ebs", {}).get("delete_on_termination") is False:
                raise ValueError("Generated EBS volumes must be disposable")
        for disk in spec.get("existing_volumes", []):
            _unknown(disk, ("volume_id", "device_name"), "existing_volumes entry")
            if (
                not disk.get("volume_id")
                or not disk.get("device_name")
                or disk["device_name"] in devices
            ):
                raise ValueError("existing_volumes require a volume_id and unique device_name")
            devices.add(disk["device_name"])
        result[name] = spec
    return result


def intent(specs):
    """Produce a secret-free immutable intent for persisted run identity."""
    clean = copy.deepcopy(specs)
    for spec in clean.values():
        spec.pop("connection_vars", None)
        spec.pop("user_data", None)
        spec.get("cloud_init", {}).pop("user_data", None)
    return clean


def token(run_id, host):
    """Bind a launch token to one logical guest and one run."""
    return hashlib.sha256((run_id + ":" + host).encode()).hexdigest()


def tags(run_id, host=None, supplied=None):
    """Protect generated ownership tags against caller overrides."""
    result = dict(supplied or {})
    result.update({RUN: run_id, OWNER: MANAGER})
    if host is not None:
        result[HOST] = host
    return result


def cloud_data(spec, public_key=""):
    """Preserve caller bootstrap while optionally injecting the management user."""
    if spec.get("user_data"):
        return spec["user_data"]
    cloud = spec["cloud_init"]
    if not cloud["enabled"]:
        return ""
    data = copy.deepcopy(cloud["user_data"])
    if isinstance(data, str):
        return data
    if cloud["mode"] == "merge":
        data.setdefault("packages", ["python3", "sudo"])
        for package in ("python3", "sudo"):
            if package not in data["packages"]:
                data["packages"].append(package)
    if spec["key_inject_method"] == "cloud-init":
        users = data.setdefault("users", [])
        user = next(
            (u for u in users if isinstance(u, dict) and u.get("name") == spec["ssh_user"]), None
        )
        if user is None:
            user = {
                "name": spec["ssh_user"],
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "shell": "/bin/bash",
            }
            users.append(user)
        keys = user.setdefault("ssh_authorized_keys", [])
        if public_key not in keys:
            keys.append(public_key)
    return data


def pascal(value):
    """Translate documented module-style device/options dictionaries to SDK keys."""
    if isinstance(value, list):
        return [pascal(item) for item in value]
    if isinstance(value, dict):
        names = {
            "kms_key_id": "KmsKeyId",
            "iops": "Iops",
            "ebs": "Ebs",
            "ipv6_addresses": "Ipv6Addresses",
            "ipv6_address": "Ipv6Address",
        }
        return {
            names.get(key, "".join(part.capitalize() for part in key.split("_"))): pascal(item)
            for key, item in value.items()
        }
    return value


def request(spec, image, subnet, groups, run_id, host, user_data, key_name="", template=None):
    """Render a single disposable RunInstances request; lifecycle remains in the role."""
    result = copy.deepcopy(template or {})
    for field in ("TagSpecifications", "SecurityGroupIds", "SecurityGroups", "UserData"):
        result.pop(field, None)
    result.update({"ImageId": image["image_id"], "InstanceType": spec["instance_type"]})
    options = spec.get("instance_options", {})
    for field in (
        "placement",
        "cpu_options",
        "ebs_optimized",
        "metadata_options",
        "instance_initiated_shutdown_behavior",
    ):
        if field in options:
            result.update(pascal({field: options[field]}))
    if "cpu_credit_specification" in options:
        result["CreditSpecification"] = {"CpuCredits": options["cpu_credit_specification"]}
    if "detailed_monitoring" in options:
        result["Monitoring"] = {"Enabled": options["detailed_monitoring"]}
    result.setdefault("MetadataOptions", {"HttpEndpoint": "enabled", "HttpTokens": "required"})
    if spec.get("iam_instance_profile"):
        profile = spec["iam_instance_profile"]
        result["IamInstanceProfile"] = {"Arn" if profile.startswith("arn:") else "Name": profile}
    if key_name:
        result["KeyName"] = key_name
    if user_data:
        result["UserData"] = user_data
    nics = []
    for index, supplied in enumerate(
        spec.get("network_interfaces_ids", []) or spec.get("network_interfaces", []) or [{}]
    ):
        if "id" in supplied:
            if supplied.get("delete_on_termination"):
                raise ValueError("Referenced ENIs must not have delete_on_termination enabled")
            nic = {
                "NetworkInterfaceId": supplied["id"],
                "DeviceIndex": supplied.get("device_index", index),
                "DeleteOnTermination": False,
            }
        else:
            nic = pascal(supplied)
            nic.update(
                {
                    "SubnetId": supplied.get("subnet_id", subnet),
                    "DeviceIndex": supplied.get("device_index", index),
                    "DeleteOnTermination": True,
                }
            )
            nic["Groups"] = supplied.get("groups") or groups
            if nic["DeviceIndex"] == 0:
                nic["AssociatePublicIpAddress"] = spec["assign_public_ip"]
                nic.pop("AssignPublicIp", None)
        nics.append(nic)
    if len({nic["DeviceIndex"] for nic in nics}) != len(nics) or 0 not in [
        nic["DeviceIndex"] for nic in nics
    ]:
        raise ValueError("Network device indexes must be unique and include primary index 0")
    result.pop("SubnetId", None)
    # EC2 allows automatic public IPv4 assignment only with one interface at launch.
    # Additional generated interfaces are attached by the role after the primary boots.
    result["NetworkInterfaces"] = (
        [nic for nic in nics if nic["DeviceIndex"] == 0]
        if spec["assign_public_ip"] and len(nics) > 1
        else nics
    )
    disks = (
        copy.deepcopy(result.get("BlockDeviceMappings", []))
        if template and not spec.get("root_disk") and not spec.get("volumes")
        else []
    )
    root = merge({"volume_type": "gp3", "encrypted": True}, spec.get("root_disk", {}))
    root_size = next(
        (
            disk.get("ebs", {}).get("volume_size", 0)
            for disk in image.get("block_device_mappings", [])
            if disk["device_name"] == image["root_device_name"]
        ),
        0,
    )
    if root.get("size", root_size) < root_size:
        raise ValueError("root_disk.size cannot shrink the AMI root volume")
    if "size" in root:
        root["volume_size"] = root.pop("size")
    root["delete_on_termination"] = True
    if not disks:
        disks.append({"device_name": image["root_device_name"], "ebs": root})
    for disk in spec.get("volumes", []):
        if disk["device_name"] == image["root_device_name"]:
            raise ValueError("Use root_disk rather than duplicate the AMI root device in volumes")
        disk = copy.deepcopy(disk)
        if "ebs" in disk:
            disk["ebs"] = merge(
                {"volume_type": "gp3", "encrypted": True, "delete_on_termination": True},
                disk["ebs"],
            )
        disks.append(disk)
    result["BlockDeviceMappings"] = (
        disks if template and disks and "DeviceName" in disks[0] else pascal(disks)
    )
    owner = tags(run_id, host, spec.get("tags"))
    owner["Name"] = "mp-" + run_id[:16] + "-" + host[:40]
    kinds = ["instance", "volume"] + (
        ["network-interface"] if any("NetworkInterfaceId" not in nic for nic in nics) else []
    )
    result["TagSpecifications"] = [
        {
            "ResourceType": kind,
            "Tags": [{"Key": key, "Value": str(value)} for key, value in owner.items()],
        }
        for kind in kinds
    ]
    if result.get("DisableApiTermination") or any(
        not disk.get("Ebs", {}).get("DeleteOnTermination", True)
        for disk in result["BlockDeviceMappings"]
    ):
        raise ValueError("Launch configuration prevents disposable cleanup")
    return result


def fingerprint(value):
    """Digest non-secret immutable structures without embedding credentials."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def valid_request(value):
    """Validate SDK request shape before provisioning without including its values in errors."""
    from botocore.session import get_session
    from botocore.validate import validate_parameters
    from botocore.exceptions import ParamValidationError

    shape = get_session().get_service_model("ec2").operation_model("RunInstances").input_shape
    try:
        validate_parameters(dict(value, MinCount=1, MaxCount=1, ClientToken="preflight"), shape)
    except ParamValidationError as error:
        raise ValueError(
            "Launch configuration does not match the supported EC2 request schema"
        ) from error
    return True


def ingress(spec):
    """Build explicit management/application ingress without implicit public access."""
    rules = copy.deepcopy(spec.get("security_group_rules", []))
    for port, protocol, cidrs in [(spec["ssh_port"], "tcp", spec["management_cidrs"])] + [
        (p["port"], p["protocol"].lower(), p.get("cidrs", [])) for p in spec["application_ports"]
    ]:
        for cidr in cidrs:
            rules.append(
                {
                    "proto": protocol,
                    "from_port": port,
                    "to_port": port,
                    "cidr_ipv6" if ":" in cidr else "cidr_ip": cidr,
                }
            )
    return rules


def template_spec(spec, template):
    """Resolve a pinned template while suppressing implicit generated bootstrap."""
    if template.get("InstanceMarketOptions"):
        raise ValueError(
            "Launch template market/Spot options are outside the disposable on-demand lifecycle"
        )
    if template.get("DisableApiTermination"):
        raise ValueError("Launch template enables termination protection")
    for disk in template.get("BlockDeviceMappings", []):
        if disk.get("Ebs", {}).get("DeleteOnTermination") is False:
            raise ValueError("Launch template retains generated disks after termination")
    for nic in template.get("NetworkInterfaces", []):
        if nic.get("NetworkInterfaceId") and nic.get("DeleteOnTermination"):
            raise ValueError("Launch template deletes a referenced ENI")
        if not nic.get("NetworkInterfaceId") and nic.get("DeleteOnTermination") is False:
            raise ValueError("Launch template retains generated ENIs")
    derived = {
        "instance_type": template.get("InstanceType", spec["instance_type"]),
        "key_inject_method": "ec2" if template.get("KeyName") else "none",
        "cloud_init": {"enabled": False},
    }
    if template.get("ImageId"):
        derived["image_id"] = template["ImageId"]
    if template.get("KeyName"):
        derived["key_name"] = template["KeyName"]
    if template.get("SubnetId"):
        derived["subnet_id"] = template["SubnetId"]
    if template.get("SecurityGroupIds") or template.get("SecurityGroups"):
        derived["security_groups"] = template.get("SecurityGroupIds") or template["SecurityGroups"]
    if template.get("NetworkInterfaces"):
        nics = template["NetworkInterfaces"]
        if any(nic.get("NetworkInterfaceId") for nic in nics):
            if not all(nic.get("NetworkInterfaceId") for nic in nics):
                raise ValueError(
                    "Mixed existing and generated template ENIs require explicit network overrides"
                )
            derived["assign_public_ip"] = False
            derived["network_interfaces_ids"] = [
                {"id": nic["NetworkInterfaceId"], "device_index": nic.get("DeviceIndex", index)}
                for index, nic in enumerate(nics)
            ]
        else:
            derived["network_interfaces"] = []
            for index, nic in enumerate(nics):
                converted = {}
                for key, value in nic.items():
                    if key in ("DeleteOnTermination", "AssociatePublicIpAddress"):
                        continue
                    field = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
                    if isinstance(value, list):
                        value = [
                            (
                                {
                                    re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower(): v
                                    for k, v in item.items()
                                }
                                if isinstance(item, dict)
                                else item
                            )
                            for item in value
                        ]
                    converted[field] = value
                converted.setdefault("device_index", index)
                derived["network_interfaces"].append(converted)
            primary = next(
                (nic for index, nic in enumerate(nics) if nic.get("DeviceIndex", index) == 0), {}
            )
            if primary.get("Groups"):
                derived["security_groups"] = primary["Groups"]
            if "AssociatePublicIpAddress" in primary:
                derived["assign_public_ip"] = primary["AssociatePublicIpAddress"]
    if template.get("UserData"):
        derived["user_data"] = template["UserData"]
    explicit = {key: spec[key] for key in spec["_explicit"]}
    if any(explicit.get(field) for field in ("image_name", "image_owner", "image_filters")):
        derived.pop("image_id", None)
    if explicit.get("cloud_init", {}).get("enabled"):
        derived.pop("user_data", None)
    if not any(field in explicit for field in ("network_interfaces", "network_interfaces_ids")):
        for nic in derived.get("network_interfaces", []):
            if "subnet_id" in explicit and nic["device_index"] == 0:
                nic["subnet_id"] = explicit["subnet_id"]
            if "security_groups" in explicit:
                nic.pop("groups", None)
    # Auto is resolved only after template network references have been discovered.
    if "network" not in explicit:
        derived["network"] = {"mode": "auto"}
    else:
        explicit["network"] = merge(explicit["network"], {"mode": spec["_network_input_mode"]})
    return normalize({"host": merge(derived, explicit)})["host"]


def image_info(images, architectures):
    """Select a deterministic AMI and retain only fields required for launch."""
    if not images:
        raise ValueError("No matching AMI found")
    selected = sorted(
        images, key=lambda item: (item.get("creation_date", ""), item["image_id"]), reverse=True
    )[0]
    if (
        selected.get("state") != "available"
        or selected.get("architecture") not in architectures
        or selected.get("root_device_type") != "ebs"
    ):
        raise ValueError("AMI must be available, EBS-backed and compatible with the instance type")
    return {
        key: selected[key]
        for key in ("image_id", "architecture", "root_device_name", "block_device_mappings")
    }


def network_plan(specs, plans):
    """Share one compatible managed network per region and choose a common AZ."""
    networks = {}
    for host, spec in specs.items():
        if spec["network"]["mode"] != "create":
            continue
        region = spec["region"]
        network = spec["network"]
        proposed = {
            "region": region,
            "profile": spec.get("profile", ""),
            "vpc_cidr": network["vpc_cidr"],
            "subnet_cidr": network["subnet_cidr"],
            "zones": plans[host]["zones"],
        }
        desired_zones = [
            network.get("availability_zone"),
            spec.get("instance_options", {}).get("placement", {}).get("availability_zone"),
        ]
        for desired in desired_zones:
            if desired:
                proposed["zones"] = [zone for zone in proposed["zones"] if zone == desired]
        if region in networks:
            prior = networks[region]
            if any(prior[key] != proposed[key] for key in ("profile", "vpc_cidr", "subnet_cidr")):
                raise ValueError(
                    "Managed hosts in one region require matching network settings/profile"
                )
            proposed["zones"] = sorted(set(prior["zones"]) & set(proposed["zones"]))
        if not proposed["zones"]:
            raise ValueError("No common enabled AZ satisfies managed network placement")
        networks[region] = proposed
    for network in networks.values():
        network["az"] = network.pop("zones")[0]
    return networks


def owned(resources, run_id):
    """Refuse any discovered resource whose ownership cannot be verified."""
    for resource in resources:
        stamp = resource.get("tags", {})
        if isinstance(stamp, list):
            stamp = {item["Key"]: item["Value"] for item in stamp}
        if stamp.get(RUN) != run_id or stamp.get(OWNER) != MANAGER:
            raise ValueError("Resource ownership mismatch; retained run state was not changed")
    return resources


def reconcile_network(saved, found, run_id, deleting=False):
    """Recover exact network IDs without adopting names or retrying an uncertain create."""
    result = copy.deepcopy(saved)
    for kind, resources in found.items():
        owned(resources, run_id)
        if len(resources) > 1:
            raise ValueError("Multiple network resources match one saved identity")
        key = {
            "vpc": "vpc_id",
            "subnet": "subnet_id",
            "gateway": "gateway_id",
            "route": "route_table_id",
        }[kind]
        if resources:
            resource = resources[0]
            resource_id = (
                resource.get(key) or resource.get("internet_gateway_id") or resource.get("id")
            )
            if saved.get(key) and saved[key] != resource_id:
                raise ValueError("Network resource ID differs from saved ownership")
            if (
                kind == "vpc"
                and saved.get("vpc_cidr")
                and resource.get("cidr_block") != saved["vpc_cidr"]
            ):
                raise ValueError("Owned VPC CIDR differs from saved intent")
            if (
                kind in ("subnet", "route")
                and saved.get("vpc_id")
                and resource.get("vpc_id") != saved["vpc_id"]
            ):
                raise ValueError("Owned network resource moved to a different VPC")
            if (
                kind == "subnet"
                and saved.get("subnet_cidr")
                and (
                    resource.get("cidr_block") != saved["subnet_cidr"]
                    or resource.get("availability_zone") != saved["az"]
                )
            ):
                raise ValueError("Owned subnet CIDR or AZ differs from saved intent")
            if kind == "gateway" and any(
                attachment.get("vpc_id") != saved.get("vpc_id", result.get("vpc_id"))
                for attachment in resource.get("attachments", [])
            ):
                raise ValueError("Owned gateway has an unexpected VPC attachment")
            if kind == "route":
                result["association_ids"] = [
                    association["route_table_association_id"]
                    for association in resource.get("associations", [])
                    if association.get("subnet_id")
                    == saved.get("subnet_id", result.get("subnet_id"))
                ]
            result[key] = resource_id
        elif saved.get(key) and not deleting:
            raise ValueError(
                "A recorded network resource disappeared; destroy the run before recreating"
            )
        if saved.get("pending") == kind:
            if not resources:
                raise ValueError(
                    "Uncertain network create; inspect AWS and resolve the pending journal entry before retrying"
                )
            result["pending"] = ""
    return result


def runtime(spec, instance, run, host, key_path="", password=""):
    """Construct the native inventory overlay and service endpoints."""
    address = spec.get("connection_ip") or instance.get(spec["connection_address"] + "_ip_address")
    if not address:
        raise ValueError("Selected instance management address is unavailable")
    windows = spec["connection"] in ("winrm", "psrp")
    values = {
        "ansible_connection": spec["connection"],
        "ansible_host": address,
        "ansible_port": spec["ssh_port"],
        "ansible_user": spec["admin_user"] if windows else spec["ssh_user"],
        "mp_aws_instance_id": instance["instance_id"],
        "mp_aws_region": spec["region"],
        "mp_aws_vpc_id": instance["vpc_id"],
        "mp_aws_subnet_id": instance["subnet_id"],
        "mp_aws_network_mode": run["hosts"][host]["network_mode"],
        "mp_aws_private_ip": instance.get("private_ip_address", ""),
        "mp_aws_public_ip": instance.get("public_ip_address", ""),
        "mp_aws_endpoints": {
            p["name"]: {
                "host": address,
                "port": p["port"],
                "guest_port": p["port"],
                "protocol": p["protocol"],
            }
            for p in spec["application_ports"]
        },
    }
    if windows and password:
        values["ansible_password"] = password
    if not windows and key_path:
        values["ansible_ssh_private_key_file"] = key_path
        values["ansible_ssh_common_args"] = (
            "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        )
    values.update(spec["connection_vars"])
    return values
