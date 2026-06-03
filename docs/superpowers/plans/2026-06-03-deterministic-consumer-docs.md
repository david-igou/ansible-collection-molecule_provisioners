# Deterministic Consumer Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile this collection's consumer docs with the maintainer's armbian reference so an agent following the README lands a pinned, auto-resolving, committed-config (deterministic) molecule setup by default.

**Architecture:** Pure documentation + example-file changes (no role/code changes). Ship four new copy-paste files under `docs/examples/` (`requirements-test.yml`, `config.yml`, `ansible.cfg`, `Makefile`), rewrite the README "Installing" section to lead with a pinned Galaxy install, add a "Running tests" section, expand the prerequisites section, add a top-level `AGENTS.md` determinism checklist, and sync `CLAUDE.md`.

**Tech Stack:** Markdown, YAML, INI (ansible.cfg), Make. Linting via `yamllint`, `pre-commit` (prettier), `ansible-lint`.

**Resolves:** GitHub issue #45.

**Decisions locked (from issue review):**

- OVMF path divergence (#5/#6 in the issue): **document the override only** — no role change. The role already exposes `mp_qemu_ovmf_code` / `mp_qemu_ovmf_vars`.
- AGENTS.md determinism checklist (#6): **yes, ship a top-level `AGENTS.md`.**
- README pinned version example: **exact pin `0.0.1-alpha`** (the only published Galaxy version; matches armbian).

**Note on the bug the issue understates:** the current README "pinned" example uses `version: ">=1.0.0,<2.0.0"`, which resolves to nothing because the only published version is `0.0.1-alpha`. Task 2 removes it.

---

### Task 1: Ship the four `docs/examples/` determinism files

These are the copy-paste sources the rewritten README and `AGENTS.md` reference. Create all four, then commit once.

**Files:**

- Create: `docs/examples/requirements-test.yml`
- Create: `docs/examples/config.yml`
- Create: `docs/examples/ansible.cfg`
- Create: `docs/examples/Makefile`

- [ ] **Step 1: Create `docs/examples/requirements-test.yml`**

```yaml
---
# Pinned test-time collection dependencies, shared by every molecule scenario in
# a consumer repo. Wired in once via extensions/molecule/config.yml.
# Copy to: extensions/molecule/requirements-test.yml
collections:
  - name: david_igou.molecule_provisioners
    version: 0.0.1-alpha
```

- [ ] **Step 2: Create `docs/examples/config.yml`**

```yaml
---
# Shared molecule config, auto-merged into EVERY scenario's molecule.yml.
# Molecule only auto-discovers this file when molecule is invoked from the
# COLLECTION ROOT with MOLECULE_GLOB set (see Makefile / the README "Running
# tests" section).
# Copy to: extensions/molecule/config.yml
dependency:
  name: galaxy
  enabled: true
  options:
    requirements-file: extensions/molecule/requirements-test.yml
```

- [ ] **Step 3: Create `docs/examples/ansible.cfg`**

```ini
# Commit this at your collection root so collection resolution and connection
# timeouts are reproducible regardless of a contributor's shell environment.
# Copy to: <collection root>/ansible.cfg
[defaults]
collections_path = ~/.ansible/collections

# The VM backends (qemu, kubevirt) produce SSH guests often managed over
# network_cli (community.routeros, community.network, ...). The default 30s
# persistent-connection timeouts are too tight for those guests.
[persistent_connection]
connect_timeout = 120
command_timeout = 120
```

- [ ] **Step 4: Create `docs/examples/Makefile`**

```makefile
# Copy to your collection root. Running from the collection root (not from
# extensions/) is what lets molecule auto-discover extensions/molecule/config.yml
# and therefore engage the pinned dependency step.
export MOLECULE_GLOB := extensions/molecule/*/molecule.yml

.PHONY: test
test:
	molecule test --all

.PHONY: converge
converge:
	molecule converge --all

.PHONY: destroy
destroy:
	molecule destroy --all
```

- [ ] **Step 5: Lint the new YAML files**

Run: `yamllint docs/examples/requirements-test.yml docs/examples/config.yml`
Expected: no output (exit 0). `.yamllint` raises line-length to 120; these are well under it.

- [ ] **Step 6: Run pre-commit on the new files (formats Makefile/markdown-adjacent, validates YAML)**

Run: `pre-commit run --files docs/examples/requirements-test.yml docs/examples/config.yml docs/examples/ansible.cfg docs/examples/Makefile`
Expected: hooks `Passed` (prettier may reformat `requirements-test.yml`/`config.yml` once; if it reports "files were modified", re-run the same command and confirm it now passes).

- [ ] **Step 7: Commit**

```bash
git add docs/examples/requirements-test.yml docs/examples/config.yml docs/examples/ansible.cfg docs/examples/Makefile
git commit -m "docs(examples): ship pinned requirements-test, config.yml, ansible.cfg, Makefile (refs #45)"
```

---

### Task 2: Rewrite the README "Installing" section

Lead with the pinned Galaxy install + the `config.yml` / `requirements-test.yml` pattern; demote git/`main` to a collapsed note. Remove the stale "Until the first Galaxy release lands" wording and the broken `>=1.0.0,<2.0.0` example.

**Files:**

- Modify: `README.md:16-42` (the entire `## Installing` section, from the `## Installing` heading through the closing ```of the`>=1.0.0,<2.0.0` block)

- [ ] **Step 1: Replace the `## Installing` section**

Use the Edit tool. `old_string` is the current block (README.md lines 16–42, starting `## Installing` and ending at the ` ``` ` that closes the `version: ">=1.0.0,<2.0.0"` example). `new_string`:

````markdown
## Installing

The collection is published on [Ansible Galaxy](https://galaxy.ansible.com/ui/repo/published/david_igou/molecule_provisioners/).
**Pin an exact version** so test runs stay deterministic — a floating `main`
can change provisioner behavior between runs with no change on your side.

The recommended pattern (used by the reference consumer
[`ansible-collection-armbian`](https://github.com/david-igou/ansible-collection-armbian))
centralizes the pin in one file shared by every scenario, instead of a
per-scenario `collections.yml` that drifts independently:

**1. Pin the version in `extensions/molecule/requirements-test.yml`:**

```yaml
collections:
  - name: david_igou.molecule_provisioners
    version: 0.0.1-alpha
```

**2. Wire it into every scenario once via `extensions/molecule/config.yml`:**

```yaml
dependency:
  name: galaxy
  enabled: true
  options:
    requirements-file: extensions/molecule/requirements-test.yml
```

Molecule auto-merges `extensions/molecule/config.yml` into every scenario — but
only when invoked from the **collection root** (see [Running tests](#running-tests)).
Both files ship ready-to-copy under [`docs/examples/`](docs/examples/).

<details>
<summary>Tracking unreleased changes (git install)</summary>

To test against unreleased changes, point at the git repo instead of a Galaxy
version. Molecule's `dependency` step also reads a scenario-local
**`collections.yml`** (not `requirements.yml`):

```yaml
collections:
  - name: https://github.com/david-igou/ansible-collection-molecule_provisioners.git
    type: git
    version: main # floats — not for reproducible runs
```

</details>
````

- [ ] **Step 2: Verify the broken pin example is gone**

Run: `grep -n '1.0.0,<2.0.0' README.md`
Expected: no output (the unresolvable example was removed).

- [ ] **Step 3: Verify the stale wording is gone**

Run: `grep -n 'Until the first Galaxy release lands' README.md`
Expected: no output.

- [ ] **Step 4: Lint the README**

Run: `pre-commit run --files README.md`
Expected: `Passed` (prettier may reformat once; re-run if it reports a modification, then confirm pass).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): lead Installing with pinned Galaxy + config.yml pattern (refs #45)"
```

---

### Task 3: Add a "Running tests" section + env-driven `mp_backend` note to the README

Document `MOLECULE_GLOB` + run-from-collection-root + the `Makefile` and `ansible.cfg`. Insert a new section between "Using" and "Controller-host prerequisites". Also add the one-line note that the env-driven `mp_backend` form is preferred (issue point #5).

**Files:**

- Modify: `README.md` — the line `Switch backends at runtime: ...` (currently README.md:127) and the `## Controller-host prerequisites by backend` heading (currently README.md:131)

- [ ] **Step 1: Add the env-driven `mp_backend` note**

Use the Edit tool. `old_string`:

```markdown
Switch backends at runtime: `PROVISIONER=podman molecule test` (or `kubevirt`, `qemu`, `docker`).
```

`new_string`:

```markdown
Switch backends at runtime: `PROVISIONER=podman molecule test` (or `kubevirt`, `qemu`, `docker`).

A single-backend scenario may hardcode `mp_backend: qemu`, but the env-driven
form above is preferred — it keeps the `PROVISIONER=<backend>` override working
and stays consistent with multi-backend repos.
```

- [ ] **Step 2: Insert the "Running tests" section before the prerequisites heading**

Use the Edit tool. `old_string`:

```markdown
## Controller-host prerequisites by backend
```

`new_string`:

````markdown
## Running tests

Molecule auto-discovers `extensions/molecule/config.yml` — which wires in the
pinned dependency (see [Installing](#installing)) — **only when invoked from the
collection root**. Run it from anywhere else and the deterministic dependency
step silently won't engage. Commit a `Makefile` at the collection root that sets
`MOLECULE_GLOB` and runs from root:

```makefile
export MOLECULE_GLOB := extensions/molecule/*/molecule.yml

.PHONY: test
test:
	molecule test --all
```

`make test` then runs every scenario. For one scenario: `molecule test -s <scenario>`.
Switch backends with `PROVISIONER=<backend> make test`.

Also commit an `ansible.cfg` at the collection root so collection resolution and
connection timeouts don't depend on each contributor's shell environment:

```ini
[defaults]
collections_path = ~/.ansible/collections

[persistent_connection]
connect_timeout = 120
command_timeout = 120
```

The `[persistent_connection]` bump matters for the `qemu` and `kubevirt`
backends: their SSH guests are frequently managed over `network_cli`
(community.routeros, community.network, …), and the default 30s timeouts are too
tight. Copies of both files ship under [`docs/examples/`](docs/examples/).

## Controller-host prerequisites by backend
````

- [ ] **Step 3: Verify both anchors the README now links to resolve**

Run: `grep -n -E '^## (Installing|Running tests)' README.md`
Expected: two lines — `## Installing` and `## Running tests` (the `[Installing](#installing)` and `[Running tests](#running-tests)` links from Tasks 2–3 resolve to these).

- [ ] **Step 4: Lint the README**

Run: `pre-commit run --files README.md`
Expected: `Passed` (re-run once if prettier reformats).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): add Running tests section (MOLECULE_GLOB, ansible.cfg) (refs #45)"
```

---

### Task 4: Expand "Controller-host prerequisites" with a copy-paste snippet + OVMF/pylibssh notes

Add a Debian/Ubuntu install snippet, the `ansible-pylibssh` note for network_cli guests, and the OVMF path-override documentation (docs only — no role change).

**Files:**

- Modify: `README.md` — immediately after the prerequisites table (the table row ending in `(`pip install docker`)` |`, currently README.md:138)

- [ ] **Step 1: Insert the snippet + notes after the prerequisites table**

Use the Edit tool. `old_string`:

```markdown
| `docker` | A reachable local docker daemon and the `docker` python package (`pip install docker`) |
```

`new_string`:

````markdown
| `docker` | A reachable local docker daemon and the `docker` python package (`pip install docker`) |

On a Debian/Ubuntu controller, the `qemu` backend's system prerequisites are:

```bash
apt-get install -y qemu-system-x86 qemu-utils cloud-image-utils ovmf sshpass
```

Notes for the VM backends (`qemu`, `kubevirt`):

- **`network_cli` guests:** install `ansible-pylibssh` (`pip install ansible-pylibssh`).
  paramiko fails to negotiate SSH with some network OSes (e.g. RouterOS); pylibssh
  is required for those guests.
- **OVMF path (qemu UEFI only):** the role defaults to the Fedora/RHEL paths
  `mp_qemu_ovmf_code: /usr/share/edk2/ovmf/OVMF_CODE.fd` and
  `mp_qemu_ovmf_vars: /usr/share/edk2/ovmf/OVMF_VARS.fd`. Debian/Ubuntu ship OVMF
  under `/usr/share/OVMF/`. Either override those two vars in `group_vars`, or
  symlink the Debian paths to the role defaults:

  ```bash
  ln -sf /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd
  ln -sf /usr/share/OVMF/OVMF_VARS.fd /usr/share/edk2/ovmf/OVMF_VARS.fd
  ```
````

- [ ] **Step 2: Confirm the documented var names match the role defaults**

Run: `grep -n -E 'mp_qemu_ovmf_(code|vars)' roles/qemu/defaults/main.yml README.md`
Expected: the same two var names and paths appear in both files (`/usr/share/edk2/ovmf/OVMF_CODE.fd` and `/usr/share/edk2/ovmf/OVMF_VARS.fd`).

- [ ] **Step 3: Lint the README**

Run: `pre-commit run --files README.md`
Expected: `Passed` (re-run once if prettier reformats).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): add backend prereqs snippet + OVMF/pylibssh notes (refs #45)"
```

---

### Task 5: Add a determinism checklist to `AGENTS.md`

A one-pass checklist an agent can follow to land the deterministic setup, cross-linking the example files and README sections from the earlier tasks.

> **Note:** `AGENTS.md` already exists at the repo root with a short ansible-creator reference (lines 1–7). **Append** the new section below — do not recreate the file or drop the existing reference.

**Files:**

- Modify: `AGENTS.md` (append the determinism checklist section after the existing ansible-creator reference)

- [ ] **Step 1: Append the determinism-checklist section to `AGENTS.md`**

Leave the existing content (the `# AGENTS.md` heading and the ansible-creator reference) untouched. Append:

```markdown

## Adding a Molecule scenario to a consumer collection

The checklist below is for agents adding a Molecule scenario to a **consumer**
collection that uses `david_igou.molecule_provisioners`. Following it yields a
**deterministic**, environment-independent test setup in one pass, matching the
maintainer's reference consumer
([`ansible-collection-armbian`](https://github.com/david-igou/ansible-collection-armbian)).

### Determinism checklist

- [ ] **Pin the provisioner version.** In `extensions/molecule/requirements-test.yml`,
      pin `david_igou.molecule_provisioners` to an exact Galaxy version (e.g.
      `0.0.1-alpha`) — never `version: main`. Copy `docs/examples/requirements-test.yml`.
- [ ] **Centralize the pin via `config.yml`.** Add `extensions/molecule/config.yml`
      pointing `dependency.options.requirements-file` at the pinned file, so every
      scenario shares one version and one bump point. Copy `docs/examples/config.yml`.
- [ ] **Run from the collection root with `MOLECULE_GLOB`.** `config.yml`
      auto-discovery fires only from the collection root. Use `docs/examples/Makefile`
      (`export MOLECULE_GLOB := extensions/molecule/*/molecule.yml`) and run `make test`.
- [ ] **Commit an `ansible.cfg`.** Set `collections_path` and bump the
      `[persistent_connection]` timeouts for `network_cli`-managed VM guests. Copy
      `docs/examples/ansible.cfg`.
- [ ] **Install backend prereqs explicitly.** See the README
      "Controller-host prerequisites by backend" section for the per-backend
      copy-paste install snippet, the `ansible-pylibssh` requirement for `network_cli`
      guests, and the OVMF path notes for `qemu` UEFI.

Do all five and re-running the same scenario a week later produces the same
provisioner behavior. See the [README](README.md) for the full consumer walkthrough.
```

- [ ] **Step 2: Lint `AGENTS.md`**

Run: `pre-commit run --files AGENTS.md`
Expected: `Passed` (re-run once if prettier reformats, then confirm pass).

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs: add AGENTS.md determinism checklist (refs #45)"
```

---

### Task 6: Sync `CLAUDE.md` with the new files

Keep the project guidance accurate now that `docs/examples/` has four new files and a top-level `AGENTS.md` exists.

**Files:**

- Modify: `CLAUDE.md` — the `docs/examples/` bullet under "### Key files" (the line beginning `- \`docs/examples/\` — copy-paste starter for consumers`)

- [ ] **Step 1: Update the `docs/examples/` description**

Use the Edit tool. `old_string`:

```markdown
- `docs/examples/` — copy-paste starter for consumers (`molecule.yml` boilerplate + `inventory/` shape).
```

`new_string`:

```markdown
- `docs/examples/` — copy-paste starter for consumers: `molecule.yml` boilerplate, `inventory/` shape, plus the deterministic-setup files (`requirements-test.yml` pinned to the Galaxy version, `config.yml` wiring it into every scenario, `ansible.cfg`, and a `MOLECULE_GLOB` `Makefile`).
- `AGENTS.md` — top-level one-pass determinism checklist for agents adding a scenario in a consumer repo (pin version, centralize via `config.yml`, run from root with `MOLECULE_GLOB`, commit `ansible.cfg`).
```

- [ ] **Step 2: Lint `CLAUDE.md`**

Run: `pre-commit run --files CLAUDE.md`
Expected: `Passed` (re-run once if prettier reformats).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): note new docs/examples determinism files + AGENTS.md (refs #45)"
```

---

### Task 7: Full-repo verification

Confirm nothing regressed across the whole tree before handing back.

- [ ] **Step 1: Run yamllint on the repo**

Run: `yamllint .`
Expected: no output (exit 0).

- [ ] **Step 2: Run ansible-lint**

Run: `ansible-lint`
Expected: `Passed` — the doc/example changes touch no playbooks or roles, so the result must match the pre-change baseline. If anything fails, it is unrelated to this plan; note it and stop.

- [ ] **Step 3: Run the full pre-commit suite**

Run: `pre-commit run --all-files`
Expected: all hooks `Passed`. If `prettier` reformats anything, re-run, then `git add -A && git commit -m "style: prettier formatting (refs #45)"`.

- [ ] **Step 4: Confirm every issue #45 deliverable is covered**

Run: `ls docs/examples/requirements-test.yml docs/examples/config.yml docs/examples/ansible.cfg docs/examples/Makefile AGENTS.md && grep -n -E '^## (Installing|Running tests)' README.md && grep -n 'ansible-pylibssh' README.md`
Expected: all five files listed, both README headings present, and the pylibssh note found.

- [ ] **Step 5: Verify the working tree is clean and commits are in place**

Run: `git status --short && git log --oneline -7`
Expected: clean working tree; the commit log shows the Task 1–6 commits (and any Task 7 formatting commit).

---

## Self-Review

**Spec coverage — issue #45's six suggested deliverables:**

1. Rewrite README "Installing" (pinned Galaxy + config.yml pattern, demote git/main) → **Task 2** ✓
2. Ship `docs/examples/config.yml` + `requirements-test.yml` (pinned) → **Task 1** ✓
3. "Running tests" section (`MOLECULE_GLOB`, run-from-root, Makefile) → **Task 3** ✓ + `Makefile` file in **Task 1**
4. Recommend committed `ansible.cfg` (collections_path + persistent_connection) → **Task 3** (docs) + **Task 1** (file) ✓
5. Per-backend prereqs/CI snippet (packages + OVMF + pylibssh) → **Task 4** ✓
6. AGENTS.md / determinism checklist → **Task 5** ✓
   - Issue point #5 (env-driven `mp_backend` note) → **Task 3, Step 1** ✓
   - OVMF: documented override only (per decision) → **Task 4** ✓
   - Broken `>=1.0.0,<2.0.0` pin removed → **Task 2** ✓
   - `CLAUDE.md` kept in sync → **Task 6** ✓

**Placeholder scan:** every file step contains complete, literal content; every command step shows the exact command and expected output. No TBD/TODO/"similar to" placeholders.

**Consistency:** the pinned version string `0.0.1-alpha` and the var names `mp_qemu_ovmf_code` / `mp_qemu_ovmf_vars` are identical across `requirements-test.yml` (Task 1), README Installing (Task 2), README prereqs (Task 4), and `AGENTS.md` (Task 5). The README anchors `#installing` and `#running-tests` referenced in Tasks 2–3 are both created and asserted present in Task 3, Step 3.
