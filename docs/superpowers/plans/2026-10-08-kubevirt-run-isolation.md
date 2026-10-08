# KubeVirt run isolation (#59)

Goal: let ordinary YAML inventories share a namespace without sharing guests.

- Enable `mp_kubevirt_run_isolation` by default in the KubeVirt role, with an
  explicit `false` override available through the dispatcher for fixed names.
  Persist a credential-free resource map before any API mutations; reuse it on
  retries, and reject inventory identity changes until destroy succeeds.
- Render run-scoped VM, disk and Service names with ownership labels. Keep
  logical inventory names and group variables unchanged. Check existing
  resources before reuse; guard deletes with UID and resourceVersion.
- Destroy from the saved map, wait for generated resources to disappear, and
  remove state only after successful cleanup. Preserve explicit fixed-name mode.
- Test rendering/state offline, then overlapping runs, connection lifecycle,
  generated disks, and interrupted/failed cleanup on OCP. Wire functional
  coverage into KubeVirt CI. Document recovery and add a changelog fragment.

Use isolated worktrees, explicit namespaces, repository-scoped GitHub auth,
block YAML, and no credentials in saved state or logs. No merge in this task.

Default change: update role defaults and both argument specs, remove explicit
opt-ins from the scenario and lifecycle tests, then run state/rendering checks,
lint and KubeVirt CI. Document the minimal inventory and fixed-name migration in
the collection README and igou-docs.
