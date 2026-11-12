# VMware

Create disposable vCenter guests through the public `create`, `prepare` and
`destroy` playbook imports. Select `mp_backend: vmware`, share inputs under
`mp_defaults.vmware`, and override them per host under `mp.vmware`.

This backend implements the contract proposed in [issue 81](https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/81).
Runtime parity requires the live acceptance evidence listed there. See
[local testing](../../docs/TESTING.md#vmware) for the current test scope.

## Requirements

The collection requires Ansible Core 2.19 or newer. Installing it also installs
`community.vmware` and `vmware.vmware` through `galaxy.yml`. Install controller-side
Python dependencies from the packaged `requirements.txt`, or let Ansible Builder
discover them. See [collection installation](../../README.md#installing).

Linux access requires an SSH client and ssh-keygen. `make ee` builds the shared
Python 3.12 test EE used by the AWS and VMware test targets.

Set `VMWARE_HOST`, `VMWARE_USER`, `VMWARE_PASSWORD`, optional `VMWARE_PORT`
and `VMWARE_VALIDATE_CERTS` in the controller environment, or supply the
`vcenter` mapping below. Explicit fields override environment values. TLS
validation defaults to true. Install the lab CA in the controller's trust store,
or explicitly set `vcenter.validate_certs: false` for a disposable lab.
All hosts in one run use one vCenter endpoint/authentication/TLS configuration.

The API identity needs inventory reads, source clone/snapshot reads, datastore
allocation, network and resource-pool assignment, VM configuration/customization,
power and removal, and Guest Operations for Tools bootstrap. Existing inventory
objects are referenced; the role does not create folders, networks or datastores.
The controller needs a route to guest IPs as well as to vCenter.

## Inventory

```yaml
---
all:
  children:
    molecule:
      vars:
        mp_backend: vmware
        mp_defaults:
          vmware:
            datacenter: lab
            cluster: compute
            folder: /lab/vm/molecule
            networks:
              - name: Test Network
                type: dhcp
            guest:
              credentials:
                username: molecule
                password: "{{ lookup('ansible.builtin.env', 'VMWARE_GUEST_PASSWORD') }}"
      hosts:
        linux:
          mp:
            vmware:
              template: linux-template
              hardware:
                num_cpus: 2
                memory_mb: 2048
```

Keep secret lookups in the caller's inventory. Roles consume resolved credentials;
no vault lookups occur inside the backend. vCenter API credentials and guest OS
credentials are separate. The journal never stores vCenter credentials or passwords.

## Host fields

| Field | Contract/default |
| --- | --- |
| `vcenter` | `hostname`, `port`, `validate_certs`, `credentials.username/password`; environment fallback above |
| `datacenter`, `folder`, `template` | Required; folder must exist. Source template or VM must resolve uniquely; an absolute source inventory path is supported |
| `cluster`, `esxi_hostname` | Exactly one, through vCenter |
| `resource_pool`, `datastore` | Optional existing placement; vendor defaults when omitted |
| `name` | Optional fixed VM display name; otherwise isolated per run. Logical inventory names remain unchanged |
| `hardware` | Vendor hardware mapping, including CPU, RAM, firmware, controllers and hot-add options |
| `networks` | Required ordered vendor list: DHCP/static IP, netmask, gateway, NIC type, standard/distributed port group and switch options |
| `snapshot_src`, `linked_clone` | Existing unique source snapshot; linked defaults true when a snapshot is set, false otherwise. Explicit false makes a full clone from that snapshot |
| `instance_os_type` | `linux` (default) or `windows` |
| `guest.credentials` | `username`, plus `password` for Tools bootstrap or Windows |
| `customization` | `hostname` (logical name), `dns_servers`; Windows `fullname`, `orgname` (both `molecule`), `autologon` (false), `autologoncount` (1). Password must match guest credentials |
| `bootstrap.method` | `tools` (default), `prepared`, or Linux-only `guestinfo` |
| `ssh_key_path` | Caller-owned private key; otherwise generate a private scenario key |
| `connection_address` | Explicit reachable address, otherwise usable IPv4 from the first NIC; ambiguous discovery fails |
| `connection_port` | Linux 22, Windows 5986; override supported |
| `connection_vars` | Additional `ansible_*` transport options. Explicit endpoint/user/key/password/plugin fields are reserved |

Mappings merge recursively: role defaults → shared defaults → host input. Lists
replace. Unknown wrapper fields, unsupported combinations, ambiguous names and
foreign ownership fail before VM mutation. Vendor check mode validates nested
hardware/network options for every host before the first allocation.

## Bootstrap and connections

**Tools:** clone-time guest customization must be supported by the source OS and
vSphere version. Linux sources need VMware/open-vm-tools, the applicable Perl
customization dependencies, SSH, Python and any required sudo configuration.
The provided existing guest account must authenticate through Guest Operations;
Linux customization does not reset its password. A generated or caller public
key is appended without replacing other authorized keys.

Windows sources must be Sysprep-ready with running Tools. Customization sets the
local Administrator password; it does not create/reset an arbitrary named user.
The privileged Guest Operations account configures HTTPS WinRM, reuses an existing
listener/certificate on retry, and opens the listener port without disabling
firewall profiles. Default transport is NTLM and the generated self-signed listener
uses certificate validation `ignore`. Prepared trusted listeners can use
`connection_vars.ansible_winrm_server_cert_validation: validate`. HTTPS is explicit,
including on custom ports. No SSH key is generated for an all-Windows run.

**Prepared:** skip Tools bootstrap commands, but still run clone customization.
Linux requires a caller key already accepted by the source. Windows requires a
prepared listener and valid guest password.

**GuestInfo:** an opt-in extension for the key-only Linux cloud templates available
in the test lab. Set `bootstrap.method: guestinfo` and a guest username; a guest
password is unnecessary. It supplies per-clone cloud-init metadata/user-data and
an SSH key before first power-on, instead of vSphere guest customization. The
source must already support the VMware cloud-init datasource. This initial mode
supports exactly one connected DHCP NIC on a named port group, with DNS from DHCP
and only `customization.hostname`. It clones powered off, sets GuestInfo, configures
the existing NIC through `vmware_guest_network`, then powers on. This avoids the
clone module inferring OS customization from a network list. Tools mode retains
the broader network contract.
It does not replace the Tools/Sysprep parity requirements.

Prepare waits for authenticated Ansible access. GuestInfo also waits for cloud-init
completion. Runtime inventory contains the selected transport, address, user, port,
key/password and `mp_vmware_instance_uuid`, `mp_vmware_moid`, `mp_vmware_name`,
`mp_vmware_bootstrap`. Native `molecule list` and Linux `molecule login` use the
same inventory. The old VMware driver's login command was SSH-only.

Disposable SSH guests default to `StrictHostKeyChecking=no`,
`UserKnownHostsFile=/dev/null`, and `IdentitiesOnly=yes`. Override
`connection_vars.ansible_ssh_common_args` for a caller-managed host-key policy.

## Controls

Set these with the other molecule group variables; public create/destroy imports
carry them into the controller play:

| Variable | Default | Scope |
| --- | --- | --- |
| `mp_vmware_provision_timeout` | 7200 | Clone worker and vendor customization/IP waits, seconds |
| `mp_vmware_tools_timeout` | 600 | Per-guest Tools readiness wait, seconds |
| `mp_vmware_wait_timeout` | 600 | Linux authenticated readiness/cloud-init waits, seconds |
| `mp_vmware_windows_wait_timeout` | 900 | Windows authenticated readiness, seconds |
| `mp_vmware_cleanup_timeout` | 7200 | Per-guest worker/task/deletion waits, seconds |
| `mp_vmware_concurrency` | 4 | Maximum guests in each concurrent clone batch |
| `mp_vmware_role_defaults` | empty mapping | Lowest-precedence host specification |

These are stage bounds, rather than a single wall-clock bound across all stages.
API inspection requests use a 30-second connection/request timeout. SDK task
completion can outlive an Ansible timeout: retain state rather than assume cancel.

## Ownership, retries and recovery

`vmware_run.yml` records non-secret run intent, source and placement, ownership
annotation, worker IDs and exact VM/disk identities. Async worker results live in
the persisted private `.vmware-async` directory. Files use mode 0600 and
private directories use 0700. A controller-process lease serializes an ephemeral
directory. Generated keys and password-bearing runtime inventory are private.

Create checkpoints submission intent before launching each asynchronous worker.
Unchanged repeated create reuses VM identities and keys and regenerates the runtime
overlay. Changed launch configuration requires destroy/create. Failed bootstrap
retains the guest for retry. Clone batches account for every recorded worker.

There are two fail-closed manual recovery windows: interrupted worker submission
before the job ID checkpoint, and a stock-module clone interrupted before its
post-clone annotation. No automatic retry, adoption or deletion is safe there.
Keep the journal and worker files, establish that the old controller/worker has
stopped, and correlate vCenter task/events, exact target folder/name, clone source
and VM identity against the saved intent. Only after ownership is independently
established should an operator restore the saved marker/worker checkpoint or
remove that disposable clone. Do not delete state or clear a submission phase just
to bypass the guard. The backend never performs a name-only sweep.

Destroy checks vCenter identity and ownership, settles recorded workers and cloud
tasks, then removes only owned clones and their own leaf disks. Source templates,
snapshots and shared infrastructure remain caller-owned. Shared/RDM/external source
disk layouts and changed clone disk lists are rejected. Removed inventory hosts
remain in saved cleanup scope. Partial cleanup prunes successful hosts from runtime
inventory and retains failed records. Caller-owned key files survive; generated
keys and journal are removed only after confirmed cleanup.

Check mode performs discovery/validation only, without keys, clone/power/Guest
Operations or journal writes. The initial implementation does not certify every
issue-81 case: Windows, snapshots/linked backing, static/distributed networks,
interruption recovery and older/current dependency combinations need their own
live evidence before the parity milestone can close.

MIT; David Igou.
