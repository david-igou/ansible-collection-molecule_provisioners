# KubeVirt run isolation (#59)

Goal: let ordinary YAML inventories share a namespace without sharing guests.

- Add opt-in `mp_kubevirt_run_isolation` to the dispatcher and KubeVirt role.
  Persist a credential-free resource map before any API mutations; reuse it on
  retries, and reject inventory identity changes until destroy succeeds.
- Render run-scoped VM, disk and Service names with ownership labels. Keep
  logical inventory names and group variables unchanged. Check existing
  resources before reuse; guard deletes with UID and resourceVersion.
- Destroy from the saved map, wait for generated resources to disappear, and
  remove state only after successful cleanup. Preserve fixed-name defaults.
- Test rendering/state offline, then overlapping runs, connection lifecycle,
  generated disks, and interrupted/failed cleanup on OCP. Wire functional
  coverage into KubeVirt CI. Document recovery and add a changelog fragment.

Use isolated worktrees, explicit namespaces, repository-scoped GitHub auth,
block YAML, and no credentials in saved state or logs. No merge in this task.
