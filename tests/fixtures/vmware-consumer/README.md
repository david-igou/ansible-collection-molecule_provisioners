# VMware consumer fixture

An installed-collection native Molecule consumer used by `make test-vmware`.
Caller environment variables select existing Linux GuestInfo templates, DHCP
network and placement. No credentials or lab selectors are committed here.

Converge creates a workload account. Verify executes a command as that account,
checks the management identity and sudo, and confirms the VMware datasource.

The local runner verifies the expected vCenter identity before creating guests,
keeps private failure state and always attempts scoped teardown. See
[VMware testing](../../../docs/TESTING.md#vmware) and the
[role reference](../../../roles/vmware/README.md).
