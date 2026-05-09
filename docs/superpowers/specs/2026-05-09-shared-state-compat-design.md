# Shared-state compatibility design

**Date:** 2026-05-09
**Status:** Draft — awaiting review
**Tracks:** [#5](https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/5)

## Goal

Decide whether `david_igou.molecule_provisioners` is compatible with Molecule's `shared_state` / shared `config.yml` pattern documented in [upstream `getting-started-collections.md`](https://github.com/ansible/molecule/blob/main/docs/getting-started-collections.md), and ship one of two deliverables based on the answer:

- **If compatible:** a `docs/examples/shared-state/` tree mirroring the upstream layout, plus README/MIGRATION/CLAUDE.md updates removing the v1.0 out-of-scope claim.
- **If not compatible:** a `docs/SHARED_STATE.md` page explaining what fails and what would unblock it, plus a README pointer to that page.

Both branches deliver a clear, consumer-facing answer. v1.0 currently lists `shared_state` as out of scope without explanation; either outcome replaces that gap with documentation.

## Why

Upstream's recommended layout for collections with multiple roles puts a single `extensions/molecule/config.yml` with `shared_state: true`, a single `extensions/molecule/inventory.yml`, and per-scenario directories that converge against shared resources:

```
extensions/molecule/
├── config.yml          # shared_state: true, executor args, playbooks
├── inventory.yml       # one shared inventory
├── default/            # creates + destroys
├── role1/              # converge-only against shared resources
└── role2/              # converge-only
```

A consumer who has internalized this pattern from upstream docs and tries to adopt this collection cannot tell from the current `docs/examples/` (per-scenario `inventory/` directory) how the two compose. The collection's "stop redefining create.yml" pitch is most attractive to multi-role collections — exactly the audience for shared_state. Leaving compatibility undocumented is the highest-friction unknown for that audience.

The technical risk is small. The dispatcher reads `mp_backend` via `hostvars[groups['molecule'][0]].mp_backend`; both roles write `${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/molecule_runtime.yml` and call `meta: refresh_inventory`. Whether those compose with shared_state's shared ephemeral directory and its `extensions/molecule/inventory.yml` location is a question only a live run answers.

## Approach

The work is two phases. Phase 1 is a time-boxed spike that produces a written verdict. Phase 2 is conditional on the verdict and ships one of two distinct deliverables.

### Phase 1 — spike

A throwaway fixture under `tests/_spike/shared-state/` (added to `.gitignore`, deleted at the end of phase 1) lets the spike exercise shared_state without polluting `extensions/molecule/`.

```
tests/_spike/shared-state/
└── extensions/molecule/
    ├── config.yml          # shared_state: true, executor args, playbooks
    ├── inventory.yml       # one host, dual-backend mp blocks
    ├── group_vars/
    │   └── molecule.yml    # mp_backend, mp_defaults
    ├── default/
    │   ├── create.yml      # import_playbook david_igou.molecule_provisioners.create
    │   ├── destroy.yml
    │   ├── prepare.yml
    │   ├── converge.yml    # debug placeholder
    │   └── verify.yml
    └── role-stub/
        ├── molecule.yml    # minimal — name only, inherits config.yml
        └── converge.yml    # debug placeholder
```

Three concrete questions, each with a binary pass/fail signal:

1. **Backend resolution.** Does `hostvars[groups['molecule'][0]].mp_backend` resolve when `mp_backend` lives in `extensions/molecule/group_vars/molecule.yml` rather than `inventory/group_vars/molecule.yml`?
   - **Test:** `cd tests/_spike/shared-state/extensions/molecule/default && PROVISIONER=podman molecule create`.
   - **Pass:** no `mp_backend must be one of...` assert failure from `playbooks/create.yml`.
   - **Fail mode:** the dispatcher's `hostvars[groups['molecule'][0]].mp_backend` returns undefined because `extensions/molecule/group_vars/` is not picked up by the inventory chain in the executor args.

2. **Runtime inventory pickup.** Does `meta: refresh_inventory` re-read `${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/molecule_runtime.yml` under shared_state, so prepare/converge connect via the runtime connection plugin?
   - **Test:** continue with `molecule test` on `default`.
   - **Pass:** converge connects via `containers.podman.podman` (podman backend) without "no hosts matched" or "ssh refused" errors.
   - **Fail mode:** the runtime inventory file is written but the next phase does not see it; converge runs against `localhost` or fails with "no hosts matched."

3. **Cross-scenario sharing.** Does the `role-stub` scenario's converge see hosts created by `default` (the whole point of shared_state)?
   - **Test:** `molecule create -s default && molecule converge -s role-stub && molecule destroy -s default`.
   - **Pass:** `role-stub` converge runs against the host without re-creating it; destroy of `default` cleans it up.
   - **Fail mode:** `role-stub` either re-runs create (defeating shared_state) or finds no hosts.

The spike output is `docs/superpowers/specs/2026-05-09-shared-state-spike-results.md` — one page, three sections, each with the question, observed result (pass/fail), and a relevant log excerpt. That doc is the input to the phase-2 branch decision.

**Kubevirt verification is best-effort.** The kubevirt-backend variant requires a live cluster with KubeVirt installed (per `KUBECONFIG`). If only podman runs, the spike-results doc must explicitly note this and the success-branch deliverable carries a "podman verified, kubevirt likely-but-untested" caveat.

### Phase 2 — success branch (all three questions pass)

Promote the spike layout into a permanent example tree:

```
docs/examples/shared-state/
└── extensions/molecule/
    ├── config.yml          # shared_state: true, executor args
    ├── inventory.yml       # dual-backend mp blocks per host
    ├── group_vars/
    │   └── molecule.yml    # mp_backend (PROVISIONER env), mp_defaults
    ├── default/
    │   ├── create.yml      # import_playbook FQCN
    │   ├── destroy.yml
    │   ├── prepare.yml
    │   ├── converge.yml    # placeholder — debug task
    │   └── verify.yml
    └── role-example/
        ├── molecule.yml    # minimal — scenario name only
        └── converge.yml    # placeholder
```

File-shape decisions locked in:

- **`config.yml`** holds `shared_state: true`, the `ansible.executor.args.ansible_playbook` chain (`--inventory=inventory.yml --inventory=${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/`), and the `playbooks:` map. Per-scenario `molecule.yml` only sets the scenario name.
- **`inventory.yml`** is a single file (not a directory) — same dual-backend host shape as today's `docs/examples/inventory/hosts.yml`, hoisted up.
- **`group_vars/molecule.yml`** carries `mp_backend` + `mp_defaults`, same shape as today's per-scenario equivalent.
- **`role-example/molecule.yml`** is minimal — proves how a sibling scenario inherits from `config.yml`.
- **Converge/verify content** is trivial (`debug` tasks) with comments pointing at "the consumer's actual role." The example demonstrates the layout, not a real role test.

A short `docs/examples/shared-state/README.md` (one of the few cases where a top-level README in an example tree is justified) explains: when to use this layout vs the per-scenario layout in `docs/examples/`, what `shared_state: true` buys, and how `PROVISIONER=podman|kubevirt molecule test` still works.

Documentation updates:

1. **`README.md` line 115** — remove the `Molecule shared_state / shared default-scenario pattern` bullet from "Out of scope for v1.0."
2. **`README.md` "Using" section** — add a one-paragraph subsection after the per-scenario walkthrough pointing at `docs/examples/shared-state/` for collection-monorepo consumers.
3. **`docs/MIGRATION.md` line 146** — remove the `Molecule's shared_state pattern` bullet.
4. **`CLAUDE.md`** — its "Out of scope" section mirrors README; update both.
5. **Historical specs** (`docs/superpowers/specs/2026-05-08-*-design.md`) — leave alone; they document v1.0's shape at the time it shipped, and this spec supersedes them on this point.

### Phase 2 — failure branch (any question fails)

No example tree. Two changes:

1. **`docs/SHARED_STATE.md`** — single page covering:
   - **What `shared_state` is.** Link to upstream `getting-started-collections.md`, one-paragraph summary.
   - **Why it doesn't work today.** Cite the specific spike question that failed and the observed behavior, with a log excerpt. Concrete, not hand-wavy.
   - **Workaround for affected consumers.** The existing per-scenario layout in `docs/examples/` works fine; the cost is a duplicated `inventory/` per scenario.
   - **What would unblock it.** One-line technical sketch (e.g., "dispatcher would need to look up `mp_backend` from `vars` instead of `hostvars`," or "runtime inventory file would need a per-scenario suffix") — enough to file a v2 follow-up issue.

2. **`README.md` line 115** — replace the bare bullet with a pointer:
   > - Molecule `shared_state` pattern — see [`docs/SHARED_STATE.md`](docs/SHARED_STATE.md) for the technical reason and the workaround.

The spike-results doc remains in `docs/superpowers/specs/` as the source-of-truth log. `docs/SHARED_STATE.md` is the consumer-facing distillation.

No README "Out of scope" line removal in this branch — the limitation stays acknowledged, just better documented.

## Acceptance criteria

**Success branch:**
- `docs/superpowers/specs/2026-05-09-shared-state-spike-results.md` exists with all three questions answered "pass" and log excerpts.
- `docs/examples/shared-state/` tree exists, parses under `ansible-lint` and `yamllint`.
- A clean run from a fresh checkout — `cd docs/examples/shared-state/extensions/molecule/default && PROVISIONER=podman molecule test` — completes the full `test_sequence`.
- `molecule converge -s role-example` after `molecule create -s default` runs without recreating hosts.
- README, MIGRATION.md, CLAUDE.md updated per the documentation list above.
- "Out of scope" bullet removed from README and MIGRATION.md.

**Failure branch:**
- Spike-results doc exists with at least one "fail" answer and observed behavior.
- `docs/SHARED_STATE.md` exists with the four sections above.
- README's out-of-scope bullet replaced with a pointer to `docs/SHARED_STATE.md`.
- No `docs/examples/shared-state/` tree (do not ship a known-broken example).

## Out of scope for this work

- A working `tests/fixtures/consumer/` runnable consumer fixture — that is [#9](https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/9).
- CI execution of the new example. Static example only; drift caught by `ansible-lint` + `yamllint` over the example tree.
- Changes to the dispatcher (`playbooks/create.yml` etc.) or roles to *make* shared_state work if it doesn't. The failure-branch deliverable explicitly defers that to a v2 follow-up issue.
- A non-`NodePort` kubevirt service-type variant of the shared-state example — out of scope per v1.0.
- Single-backend variant of the example. That is [#4](https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/4)'s territory.

## Risks

- **Kubevirt-only failure modes go undetected** if the spike runs only against podman. Mitigation: the spike-results doc must explicitly state the kubevirt result was not verified, and `docs/examples/shared-state/README.md` carries the same caveat in the success branch. A follow-up issue tracks running the kubevirt spike when a cluster is available.
- **Spike succeeds locally but breaks under a different molecule version.** `test-requirements.txt` pins molecule; the spike must use the pinned version. CI doesn't exercise the example, so a future molecule bump could regress the layout silently. Mitigation: documented in the success-branch README; fold into [#7](https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/7)'s smoke-test scope if appropriate.
- **`extensions/molecule/inventory.yml` parsing semantics.** Upstream pattern uses a single inventory file. The dispatcher's host-shape assertions assume `groups['molecule']` and `hostvars[item].mp[_mp_backend]` resolve normally — they should, but a single-file inventory plus group_vars in a sibling directory is a less-common composition than `inventory/{hosts.yml, group_vars/}`. Spike question 1 explicitly tests this.
