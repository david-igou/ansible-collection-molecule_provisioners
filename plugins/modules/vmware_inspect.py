#!/usr/bin/python
# Copyright: (c) 2026, David Igou
# SPDX-License-Identifier: MIT OR GPL-3.0-or-later
# GNU General Public License v3.0+ (https://www.gnu.org/licenses/gpl-3.0.txt).
"""Read-only vCenter preflight and ownership inspection."""

DOCUMENTATION = r"""
---
module: vmware_inspect
short_description: Resolve VMware placement and verify run ownership without mutation
description:
  - Internal read-only helper for the VMware provisioner role.
  - Resolves unique inventory objects and checks exact saved VM identities.
  - Requires pyVmomi on the controller and access to vCenter inventory.
author:
  - David Igou (@david-igou)
version_added: 0.0.7
options:
  vcenter:
    description: Endpoint, port, TLS policy and caller-resolved credentials. Uses VMWARE environment defaults.
    type: dict
    default: {}
    suboptions:
      hostname:
        description: vCenter hostname; defaults to VMWARE_HOST.
        type: str
      port:
        description: vCenter API port; defaults to VMWARE_PORT or 443.
        type: int
      validate_certs:
        description: Validate TLS; defaults to VMWARE_VALIDATE_CERTS or true.
        type: bool
      credentials:
        description: API credentials; defaults to VMWARE_USER and VMWARE_PASSWORD.
        type: dict
        suboptions:
          username:
            description: vCenter username.
            type: str
          password:
            description: vCenter password.
            type: str
  specs:
    description: Normalized host specifications for placement discovery; omitted for teardown inspection.
    type: dict
    default: {}
  records:
    description: Non-secret saved ownership records keyed by inventory host.
    type: dict
    default: {}
  expected_vcenter_uuid:
    description: Required identity on resume and teardown.
    type: str
    default: ''
  timeout:
    description: API request timeout in seconds.
    type: int
    default: 30
attributes:
  check_mode:
    description: Always performs read-only inventory inspection.
    support: full
"""
EXAMPLES = r"""
- name: Inspect owned VMware guests
  david_igou.molecule_provisioners.vmware_inspect:
    expected_vcenter_uuid: '{{ saved_run.vcenter_uuid }}'
    records: '{{ saved_run.hosts }}'
"""
RETURN = r"""
vcenter_uuid:
  description: Connected vCenter instance identity.
  type: str
  returned: success
version:
  description: vCenter version.
  type: str
  returned: success
plans:
  description: Resolved non-secret source and placement identities per host.
  type: dict
  returned: success
observations:
  description: Ownership-checked VM identity, management addresses, disk paths and Tools status.
  type: dict
  returned: success
"""

import os
import ssl

from ansible.module_utils.basic import AnsibleModule

try:
    from pyVim.connect import Disconnect, SmartConnect
    from pyVmomi import vim

    HAS_VMOMI = True
except ImportError:
    HAS_VMOMI = False


def inventory_path(obj):
    """Return the absolute vCenter inventory path without the root folder."""
    names = []
    while obj.parent is not None:
        names.append(obj.name)
        obj = obj.parent
    return "/" + "/".join(reversed(names))


def unique(objects, selector, label):
    """Never resolve an ambiguous name by first match."""
    matches = [
        o
        for o in objects
        if inventory_path(o) == selector or ("/" not in selector and o.name == selector)
    ]
    if len(matches) != 1:
        raise ValueError(
            "%s must resolve uniquely; use a supported qualified path or correct the selector"
            % label
        )
    return matches[0]


def view(content, root, types):
    """Read an inventory view and always release its server-side handle."""
    container = content.viewManager.CreateContainerView(root, types, True)
    try:
        return list(container.view)
    finally:
        container.Destroy()


def snapshot(vm, name):
    """Require exactly one named snapshot, including nested snapshots."""

    def walk(nodes):
        return [node for n in nodes for node in [n] + walk(n.childSnapshotList)]

    snapshots = walk(vm.snapshot.rootSnapshotList) if vm.snapshot else []
    matches = [node for node in snapshots if node.name == name]
    if len(matches) != 1:
        raise ValueError("snapshot_src must resolve to exactly one source snapshot")
    return matches[0].snapshot._moId


def disk_files(vm):
    """Read leaf disks only; a linked clone's parent remains caller-owned."""
    return [
        d.backing.fileName
        for d in vm.config.hardware.device
        if isinstance(d, vim.vm.device.VirtualDisk)
    ]


def safe_disks(vm):
    """Reject shared/RDM disks and leaves outside the VM's own directory."""
    directory = vm.config.files.vmPathName.rsplit("/", 1)[0] + "/"
    for disk in vm.config.hardware.device:
        if isinstance(disk, vim.vm.device.VirtualDisk):
            backing = disk.backing
            if (
                not isinstance(backing, vim.vm.device.VirtualDisk.FlatVer2BackingInfo)
                or getattr(backing, "sharing", None) == "sharingMultiWriter"
                or not backing.fileName.startswith(directory)
            ):
                return False
    return True


def plan(content, spec):
    """Discover source and placement without changing existing infrastructure."""
    dc = unique(
        view(content, content.rootFolder, [vim.Datacenter]), spec["datacenter"], "datacenter"
    )
    dc_path = inventory_path(dc)
    folder = spec["folder"]
    if folder.startswith("/vm"):
        folder = dc_path + folder
    elif not folder.startswith(dc_path + "/vm"):
        folder = dc_path + "/vm/" + folder.strip("/")
    target = unique([dc.vmFolder] + view(content, dc.vmFolder, [vim.Folder]), folder, "folder")
    source = unique(view(content, dc.vmFolder, [vim.VirtualMachine]), spec["template"], "template")
    if spec.get("bootstrap", {}).get("method") == "guestinfo":
        nics = [
            d
            for d in source.config.hardware.device
            if isinstance(d, vim.vm.device.VirtualEthernetCard)
        ]
        if len(nics) != 1:
            raise ValueError("GuestInfo sources must have exactly one existing NIC")
    if not safe_disks(source):
        raise ValueError("Source has shared, external or unsupported disk backing")
    compute = unique(
        view(
            content,
            dc.hostFolder,
            [vim.ClusterComputeResource] if spec.get("cluster") else [vim.HostSystem],
        ),
        spec.get("cluster") or spec["esxi_hostname"],
        "compute placement",
    )
    hosts = list(compute.host) if spec.get("cluster") else [compute]
    pools = view(content, compute if spec.get("cluster") else compute.parent, [vim.ResourcePool])
    if spec.get("resource_pool"):
        unique(pools, spec["resource_pool"], "resource_pool")
    stores = [o for o in dc.datastore if any(o in h.datastore for h in hosts)]
    if spec.get("datastore"):
        unique(stores, spec["datastore"], "datastore")
    networks = list({n._moId: n for h in hosts for n in h.network}.values())
    for net in spec["networks"]:
        choices = networks
        if net.get("dvswitch_name"):
            choices = [
                n
                for n in choices
                if hasattr(n, "config")
                and hasattr(n.config, "distributedVirtualSwitch")
                and n.config.distributedVirtualSwitch.name == net["dvswitch_name"]
            ]
        if net.get("name"):
            unique(choices, net["name"], "network")
        else:
            names = {
                p.spec.name
                for h in hosts
                for p in h.config.network.portgroup
                if p.spec.vlanId == net["vlan"]
            }
            if len([n for n in choices if n.name in names]) != 1:
                raise ValueError("VLAN network must resolve uniquely on selected compute")
    return {
        "folder": inventory_path(target),
        "template": inventory_path(source),
        "source_moid": source._moId,
        "snapshot_moid": snapshot(source, spec["snapshot_src"]) if spec.get("snapshot_src") else "",
    }


def observe(content, records):
    """Require exact marker/UUID and safe disks before reporting an owned guest."""
    vms = view(content, content.rootFolder, [vim.VirtualMachine])
    result = {}
    for host, record in records.items():
        matches = (
            [vm for vm in vms if vm.config.instanceUuid == record.get("instance_uuid")]
            if record.get("instance_uuid")
            else [
                vm
                for vm in vms
                if vm.name == record["name"]
                and inventory_path(vm.parent) == record["plan"]["folder"]
            ]
        )
        if not matches:
            collisions = [
                vm
                for vm in vms
                if vm.name == record["name"]
                and inventory_path(vm.parent) == record["plan"]["folder"]
            ]
            if collisions:
                raise ValueError(
                    "Saved UUID is absent but its name is occupied; manual recovery required"
                )
            result[host] = {"exists": False}
            continue
        if len(matches) != 1 or matches[0].config.annotation != record["annotation"]:
            raise ValueError(
                "VM ownership is missing/ambiguous; retain vmware_run.yml for operator recovery"
            )
        vm = matches[0]
        if not safe_disks(vm) or (
            record.get("disks") is not None and sorted(record["disks"]) != sorted(disk_files(vm))
        ):
            raise ValueError("VM disk ownership changed; refusing destructive cleanup")
        nics = [
            d for d in vm.config.hardware.device if isinstance(d, vim.vm.device.VirtualEthernetCard)
        ]
        ips = [
            ip
            for net in vm.guest.net
            if nics and net.deviceConfigId == nics[0].key
            for ip in net.ipAddress
        ]
        result[host] = {
            "exists": True,
            "name": vm.name,
            "instance_uuid": vm.config.instanceUuid,
            "moid": vm._moId,
            "management_ips": ips,
            "tools_running": vm.guest.toolsRunningStatus == "guestToolsRunning",
            "pending_tasks": [
                t._moId for t in vm.recentTask if str(t.info.state) in ("queued", "running")
            ],
            "disks": disk_files(vm),
            "primary_mac": nics[0].macAddress if nics else "",
        }
    return result


def main():
    """Run read-only discovery with bounded API requests and redacted failures."""
    module = AnsibleModule(
        argument_spec={
            "vcenter": {"type": "dict", "default": {}, "no_log": True},
            "specs": {"type": "dict", "default": {}},
            "records": {"type": "dict", "default": {}},
            "expected_vcenter_uuid": {"type": "str", "default": ""},
            "timeout": {"type": "int", "default": 30},
        },
        supports_check_mode=True,
    )
    if not HAS_VMOMI:
        module.fail_json(msg="Install the collection's requirements.txt on the controller")
    si = None
    try:
        vc = module.params["vcenter"]
        credentials = vc.get("credentials", {})
        hostname = vc.get("hostname") or os.environ.get("VMWARE_HOST")
        username = credentials.get("username") or os.environ.get("VMWARE_USER")
        password = credentials.get("password") or os.environ.get("VMWARE_PASSWORD")
        if not hostname or not username or not password:
            raise ValueError(
                "Supply vCenter endpoint and credentials through inventory or VMWARE environment"
            )
        validate = vc.get(
            "validate_certs",
            os.environ.get("VMWARE_VALIDATE_CERTS", "true").lower() not in ("false", "0", "no"),
        )
        context = ssl.create_default_context() if validate else ssl._create_unverified_context()
        si = SmartConnect(
            host=hostname,
            port=vc.get("port", int(os.environ.get("VMWARE_PORT", "443"))),
            user=username,
            pwd=password,
            sslContext=context,
            httpConnectionTimeout=module.params["timeout"],
        )
        content = si.RetrieveContent()
        expected = module.params["expected_vcenter_uuid"]
        if expected and content.about.instanceUuid != expected:
            raise ValueError("Connected vCenter identity differs from saved/expected identity")
        plans = {host: plan(content, spec) for host, spec in module.params["specs"].items()}
        observations = observe(content, module.params["records"])
        result = dict(
            changed=False,
            vcenter_uuid=content.about.instanceUuid,
            version=content.about.version,
            plans=plans,
            observations=observations,
        )
    except ValueError as error:
        module.fail_json(msg=str(error))
    except Exception as error:  # SDK faults can include credentials or customization payloads.
        module.fail_json(
            msg="VMware read-only inspection failed (%s); check endpoint, access and TLS"
            % type(error).__name__
        )
    finally:
        if si is not None:
            Disconnect(si)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
