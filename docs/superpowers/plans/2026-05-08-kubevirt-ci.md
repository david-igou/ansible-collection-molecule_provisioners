# KubeVirt CI Test Job — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub Actions job and matching molecule scenario so the kubevirt provisioner is exercised end-to-end on every PR (kind cluster + KubeVirt in `useEmulation` mode + single fedora-cloud VM).

**Architecture:** New molecule scenario `extensions/molecule/kubevirt_ci/` (single small platform, sized for emulation). New `kubevirt` job in `.github/workflows/tests.yml` that boots a kind cluster via `helm/kind-action`, applies KubeVirt manifests with `useEmulation: true`, then runs `pytest -k kubevirt_ci`. Job is required for merge via `all_green`.

**Tech Stack:** GitHub Actions, kind, KubeVirt operator manifests, molecule, pytest-ansible, fedora-cloud-container-disk-demo containerDisk.

**Branch:** `kubevirt-ci` (already created; spec already committed there).

**Honest note on TDD applicability:** This work is YAML/CI infrastructure. There are no unit-testable functions to TDD against. The verification loop is:

1. Local lint (ansible-lint, yamllint, pre-commit) — fast, runs on every commit.
2. CI on the draft PR — the real integration test, runs after Task 7 opens the PR.

Each task ends with a commit. Iterate by pushing and watching CI logs; failures are debugged against `kubectl describe` output collected by the diagnostics step in the workflow itself.

**Spec reference:** `docs/superpowers/specs/2026-05-08-kubevirt-ci-design.md`.

---

## File Structure

**Create:**

- `extensions/molecule/kubevirt_ci/molecule.yml` — scenario config (single platform, fedora-cloud demo image, NodePort SSH)
- `extensions/molecule/kubevirt_ci/create.yml` — `import_playbook` one-liner
- `extensions/molecule/kubevirt_ci/destroy.yml` — `import_playbook` one-liner
- `extensions/molecule/kubevirt_ci/prepare.yml` — `import_playbook` one-liner
- `extensions/molecule/kubevirt_ci/converge.yml` — no-op converge (mirrors existing kubevirt scenario)
- `extensions/molecule/kubevirt_ci/verify.yml` — assert `/usr/bin/sudo` exists (mirrors podman verify)
- `extensions/molecule/kubevirt_ci/group_vars/all.yml` — `ansible_connection: ssh`
- `changelogs/fragments/kubevirt-ci.yml` — `trivial:` fragment to satisfy changelog gate

**Modify:**

- `.github/workflows/tests.yml` — add `kubevirt` job; add `kubevirt` to `all_green.needs` and to its python result-set check
- `CLAUDE.md` — add command-table row pointing at `pytest -k kubevirt_ci`

---

## Task 1: Create scenario lifecycle one-liners + group_vars

**Files:**

- Create: `extensions/molecule/kubevirt_ci/create.yml`
- Create: `extensions/molecule/kubevirt_ci/destroy.yml`
- Create: `extensions/molecule/kubevirt_ci/prepare.yml`
- Create: `extensions/molecule/kubevirt_ci/converge.yml`
- Create: `extensions/molecule/kubevirt_ci/verify.yml`
- Create: `extensions/molecule/kubevirt_ci/group_vars/all.yml`

These are the trivial dispatcher hooks. Verify is the only non-trivial one — it mirrors the podman scenario (assert `/usr/bin/sudo` exists), which proves cloud-init created the user with sudo.

- [ ] **Step 1: Confirm branch state**

```bash
git status
git branch --show-current
```

Expected: clean tree on branch `kubevirt-ci`.

- [ ] **Step 2: Create the scenario directory**

```bash
mkdir -p extensions/molecule/kubevirt_ci/group_vars
```

- [ ] **Step 3: Write `create.yml`**

```yaml
---
- name: Create VMs
  import_playbook: david_igou.molecule_provisioners.create
```

- [ ] **Step 4: Write `destroy.yml`**

```yaml
---
- name: Destroy VMs
  import_playbook: david_igou.molecule_provisioners.destroy
```

- [ ] **Step 5: Write `prepare.yml`**

```yaml
---
- name: Prepare VMs
  import_playbook: david_igou.molecule_provisioners.prepare
```

- [ ] **Step 6: Write `converge.yml`**

```yaml
---
- name: Verify each host is reachable
  hosts: molecule
  gather_facts: false
  tasks:
    - name: Ping
      ansible.builtin.ping:
```

- [ ] **Step 7: Write `verify.yml`**

```yaml
---
- name: Verify each host has sudo installed (prepare side-effect)
  hosts: molecule
  gather_facts: false
  tasks:
    - name: Stat /usr/bin/sudo
      ansible.builtin.stat:
        path: /usr/bin/sudo
      register: __mp_sudo_stat
    - name: Assert sudo is installed
      ansible.builtin.assert:
        that: __mp_sudo_stat.stat.exists
        fail_msg: "/usr/bin/sudo missing — prepare phase did not install sudo"
```

- [ ] **Step 8: Write `group_vars/all.yml`**

```yaml
---
ansible_connection: ssh
```

- [ ] **Step 9: Commit**

```bash
git add extensions/molecule/kubevirt_ci/
git commit -m "Add kubevirt_ci scenario lifecycle hooks

One-line import_playbook dispatchers, no-op converge, sudo-presence
verify, and ssh connection group_vars. molecule.yml comes in the
next commit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Create `molecule.yml` for kubevirt_ci scenario

**Files:**

- Create: `extensions/molecule/kubevirt_ci/molecule.yml`

Scenario config. Single platform, small fedora-cloud-container-disk-demo image, sized for emulation runtime.

- [ ] **Step 1: Write `molecule.yml`**

```yaml
---
driver:
  name: default
  options:
    managed: true
    ansible_connection_options:
      connection: ssh

platforms:
  - name: fedora-emu
    kubevirt:
      image: quay.io/kubevirt/fedora-cloud-container-disk-demo:latest
      namespace: "${MOLECULE_NAMESPACE:-molecule}"
      ssh_service:
        type: NodePort
      ansible_user: cloud-user
      memory: 1Gi
      disk_size: 5Gi

provisioner:
  name: ansible
  playbooks:
    create: create.yml
    destroy: destroy.yml
    prepare: prepare.yml
    converge: converge.yml
    verify: verify.yml
  env:
    PROVISIONER: kubevirt
  inventory:
    links:
      group_vars: group_vars/

verifier:
  name: ansible

scenario:
  name: kubevirt_ci
  test_sequence:
    - dependency
    - syntax
    - create
    - prepare
    - converge
    - verify
    - destroy
```

- [ ] **Step 2: Lint the new scenario locally**

```bash
yamllint extensions/molecule/kubevirt_ci/
ansible-lint extensions/molecule/kubevirt_ci/
```

Expected: both pass with no warnings. If `ansible-lint` reports `name[play]` for any file, double-check the `name:` keys on the import_playbook one-liners (Task 1 Steps 3-5).

- [ ] **Step 3: Commit**

```bash
git add extensions/molecule/kubevirt_ci/molecule.yml
git commit -m "Add kubevirt_ci scenario molecule.yml

Single-platform scenario tuned for emulation: small fedora-cloud
demo containerdisk, 1Gi memory, 5Gi disk, NodePort SSH. Used by
the new kubevirt CI job to exercise the kubevirt provisioner
end-to-end without needing /dev/kvm on the runner.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Add `kubevirt` job to `.github/workflows/tests.yml`

**Files:**

- Modify: `.github/workflows/tests.yml`

Insert the new job before the `all_green` job and wire it into `all_green`'s `needs:` list and python result-set check.

- [ ] **Step 1: Read the current workflow to locate the insertion point**

```bash
grep -n "^  all_green:" .github/workflows/tests.yml
```

Expected: a line number (currently around line 65). The new `kubevirt:` job is inserted immediately above this `all_green:` line.

- [ ] **Step 2: Insert the new job**

Insert the following YAML block immediately before the `all_green:` block (preserving 2-space indentation; this is at job-level, same indent as `integration:`):

```yaml
kubevirt:
  runs-on: ubuntu-latest
  timeout-minutes: 45
  steps:
    - name: Checkout into the canonical collection path
      uses: actions/checkout@v4
      with:
        path: ansible_collections/david_igou/molecule_provisioners

    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: "3.11"

    - name: Install ansible + molecule + pytest plumbing
      working-directory: ansible_collections/david_igou/molecule_provisioners
      run: |
        python -m pip install --upgrade pip
        pip install ansible-core molecule "molecule-plugins[podman]" \
                    pytest pytest-ansible pytest-xdist kubernetes

    - name: Install collection dependencies
      working-directory: ansible_collections/david_igou/molecule_provisioners
      run: |
        ansible-galaxy collection install \
          containers.podman kubernetes.core community.crypto

    - name: Create kind cluster
      uses: helm/kind-action@v1
      with:
        version: v0.24.0
        cluster_name: kubevirt-ci
        wait: 120s

    - name: Install KubeVirt operator + CR (emulation mode)
      run: |
        KUBEVIRT_VERSION=$(curl -fsSL https://api.github.com/repos/kubevirt/kubevirt/releases/latest | jq -r .tag_name)
        echo "Installing KubeVirt ${KUBEVIRT_VERSION}"
        kubectl apply -f "https://github.com/kubevirt/kubevirt/releases/download/${KUBEVIRT_VERSION}/kubevirt-operator.yaml"
        kubectl apply -f "https://github.com/kubevirt/kubevirt/releases/download/${KUBEVIRT_VERSION}/kubevirt-cr.yaml"
        kubectl -n kubevirt patch kubevirt kubevirt --type=merge \
          -p '{"spec":{"configuration":{"developerConfiguration":{"useEmulation":true}}}}'
        kubectl -n kubevirt wait --for=condition=Available kv/kubevirt --timeout=10m

    - name: Run kubevirt_ci scenario
      working-directory: ansible_collections/david_igou/molecule_provisioners
      env:
        PROVISIONER: kubevirt
        MOLECULE_KUBEVIRT_ENABLED: "1"
        ANSIBLE_COLLECTIONS_PATH: ${{ github.workspace }}
      run: pytest tests/integration -v -k kubevirt_ci

    - name: Collect cluster diagnostics on failure
      if: failure()
      run: |
        kubectl get all -A
        kubectl -n kubevirt describe kv kubevirt
        kubectl -n molecule describe vm,vmi,pod,svc 2>/dev/null || true
        kubectl -n molecule logs -l kubevirt.io=virt-launcher --tail=200 2>/dev/null || true
```

- [ ] **Step 3: Wire `kubevirt` into `all_green.needs:`**

Edit the `all_green:` block. The current `needs:` list is:

```yaml
needs:
  - changelog
  - build-import
  - sanity
  - unit-galaxy
  - unit-source
  - ansible-lint
  - integration
```

Add `- kubevirt` so it becomes:

```yaml
needs:
  - changelog
  - build-import
  - sanity
  - unit-galaxy
  - unit-source
  - ansible-lint
  - integration
  - kubevirt
```

- [ ] **Step 4: Wire `kubevirt` into the python result-set check**

The current check is:

```yaml
- run: >-
    python -c "assert 'failure' not in
    set([
    '${{ needs.changelog.result }}',
    '${{ needs.sanity.result }}',
    '${{ needs.unit-galaxy.result }}',
    '${{ needs.ansible-lint.result }}',
    '${{ needs.unit-source.result }}',
    '${{ needs.integration.result }}'
    ])"
```

Add `'${{ needs.kubevirt.result }}',` before the closing `])`. Final shape:

```yaml
- run: >-
    python -c "assert 'failure' not in
    set([
    '${{ needs.changelog.result }}',
    '${{ needs.sanity.result }}',
    '${{ needs.unit-galaxy.result }}',
    '${{ needs.ansible-lint.result }}',
    '${{ needs.unit-source.result }}',
    '${{ needs.integration.result }}',
    '${{ needs.kubevirt.result }}'
    ])"
```

- [ ] **Step 5: Lint the workflow YAML**

```bash
yamllint .github/workflows/tests.yml
```

Expected: pass. If `actionlint` is installed (`which actionlint`), also run:

```bash
actionlint .github/workflows/tests.yml
```

Expected: no issues.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: add kubevirt job that runs kubevirt_ci scenario on kind

Boots a kind cluster, applies the KubeVirt operator and CR with
useEmulation: true, then runs the kubevirt_ci molecule scenario
via pytest. Required for merge via all_green.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Add changelog fragment

**Files:**

- Create: `changelogs/fragments/kubevirt-ci.yml`

The repo's `changelog` job (reusable from `ansible/ansible-content-actions`) requires a fragment under `changelogs/fragments/` for any PR. CI-only changes should use the `trivial:` section so they don't appear in the user-facing changelog.

- [ ] **Step 1: Write the fragment**

```yaml
---
trivial:
  - Added a CI job that runs the kubevirt provisioner end-to-end on a kind
    cluster with KubeVirt in software-emulation mode. New
    ``extensions/molecule/kubevirt_ci/`` scenario backs the job. No
    user-facing changes.
```

- [ ] **Step 2: Commit**

```bash
git add changelogs/fragments/kubevirt-ci.yml
git commit -m "Add changelog fragment for kubevirt CI job

Trivial fragment so the changelog gate passes; no user-visible
changes (CI-only).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Update `CLAUDE.md` command table

**Files:**

- Modify: `CLAUDE.md`

Add a row to the command table pointing at the new pytest selector. Locate the existing table around line 34-43.

- [ ] **Step 1: Edit `CLAUDE.md`**

Find this row in the command table:

```markdown
| Run kubevirt self-test (needs cluster) | `MOLECULE_KUBEVIRT_ENABLED=1 pytest tests/integration -v -k kubevirt` |
```

Replace it with two rows (the original local-cluster one disambiguated, plus the new CI fixture one):

```markdown
| Run kubevirt self-test against a real cluster | `MOLECULE_KUBEVIRT_ENABLED=1 pytest tests/integration -v -k "kubevirt and not ci"` |
| Run kubevirt_ci self-test (CI fixture; needs kind+KubeVirt) | `MOLECULE_KUBEVIRT_ENABLED=1 pytest tests/integration -v -k kubevirt_ci` |
```

Also update the "Key files" section. Locate this line (around line 27):

```markdown
- `extensions/molecule/{podman,kubevirt}/` — self-test scenarios. Discovered by `pytest_ansible.molecule_scenario` fixture in `tests/integration/test_integration.py`.
```

Replace with:

```markdown
- `extensions/molecule/{podman,kubevirt,kubevirt_ci}/` — self-test scenarios. Discovered by `pytest_ansible.molecule_scenario` fixture in `tests/integration/test_integration.py`. `kubevirt/` targets a developer-provisioned cluster; `kubevirt_ci/` is the in-CI fixture (kind + emulation).
```

Update the "CI" section (around line 95):

```markdown
`.github/workflows/tests.yml` runs the reusable workflows from `ansible/ansible-content-actions` (changelog, build-import, ansible-lint, sanity, unit-galaxy) plus `unit-source` and an `integration` job that exercises the podman scenario via pytest. `release.yml` publishes to Galaxy on GitHub release.
```

Replace with:

```markdown
`.github/workflows/tests.yml` runs the reusable workflows from `ansible/ansible-content-actions` (changelog, build-import, ansible-lint, sanity, unit-galaxy) plus `unit-source`, an `integration` job that exercises the podman scenario via pytest, and a `kubevirt` job that exercises the kubevirt scenario on an in-CI kind cluster with KubeVirt in `useEmulation` mode. `release.yml` publishes to Galaxy on GitHub release.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for kubevirt_ci scenario and CI job

Disambiguate local vs CI kubevirt scenarios in the command table,
list kubevirt_ci as a known scenario directory, and document the
new kubevirt CI job.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Local validation pass

**Files:** none (lint and pre-commit only)

Run all the linters the repo's pre-commit and CI use, before pushing. Catches issues without burning a CI cycle.

- [ ] **Step 1: Run yamllint over everything**

```bash
yamllint .
```

Expected: pass. Common failures:

- Line-length: `.yamllint` raises the limit to 120; if you still exceed it, wrap the line.
- Indent: 2-space indent, list dashes flush with key indentation.

- [ ] **Step 2: Run ansible-lint**

```bash
ansible-lint
```

Expected: pass. Common failures and fixes:

- `name[play]` on `import_playbook` → already named in Task 1 (Steps 3-5).
- `var-naming[no-role-prefix]` → globally skipped; if it surfaces, check `.ansible-lint`.

- [ ] **Step 3: Run pre-commit**

```bash
pre-commit run --all-files
```

Expected: pass. The hooks include `update-docs`, `prettier`, `isort`, `black`, `flake8`, plus `no-commit-to-branch` against `main`. Since we're on `kubevirt-ci`, `no-commit-to-branch` does not block.

If `update-docs` (collection_prep) modifies files, stage and amend or add a follow-up commit:

```bash
git add -A
git commit -m "Apply collection_prep update-docs"
```

- [ ] **Step 4: Verify the collection still builds**

```bash
ansible-galaxy collection build --output-path /tmp/mp-build
ls -la /tmp/mp-build/
```

Expected: a `david_igou-molecule_provisioners-1.0.0.tar.gz` file appears, no errors. Clean up:

```bash
rm -rf /tmp/mp-build
```

- [ ] **Step 5: No commit unless lint added changes**

If pre-commit auto-fixed and you committed in Step 3 sub-step, that commit covers it. Otherwise nothing to commit here — proceed.

---

## Task 7: Open draft PR and iterate on CI

**Files:** none (PR work via `gh`)

This is where the design meets reality. Open a draft PR, watch the kubevirt job, fix issues, push.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin kubevirt-ci
```

Expected: branch `kubevirt-ci` published to origin.

- [ ] **Step 2: Open a draft PR**

```bash
gh pr create --draft --title "ci: add kubevirt provisioner test job (kind + emulation)" --body "$(cat <<'EOF'
## Summary
- New ``kubevirt`` GitHub Actions job that boots a kind cluster, installs KubeVirt with ``useEmulation: true``, and runs the new ``kubevirt_ci`` molecule scenario end-to-end.
- New ``extensions/molecule/kubevirt_ci/`` scenario: single platform, ``quay.io/kubevirt/fedora-cloud-container-disk-demo:latest``, sized for emulation.
- Required for merge via ``all_green``.

Spec: ``docs/superpowers/specs/2026-05-08-kubevirt-ci-design.md``
Plan: ``docs/superpowers/plans/2026-05-08-kubevirt-ci.md``

## Test plan
- [ ] ``ansible-lint`` passes
- [ ] ``yamllint`` passes
- [ ] ``pre-commit run --all-files`` passes
- [ ] ``unit-source`` job passes
- [ ] ``integration`` (podman) job still passes
- [ ] ``kubevirt`` job runs the full create → prepare → converge → verify → destroy cycle on a single fedora-cloud VM under emulation
- [ ] ``all_green`` is green
EOF
)"
```

Expected: a draft PR URL printed.

- [ ] **Step 3: Watch the kubevirt job**

```bash
gh pr checks --watch
```

Or to inspect a specific failed job's logs:

```bash
gh run list --branch kubevirt-ci --limit 1
gh run view <run-id> --log-failed
```

- [ ] **Step 4: Iterate on failures**

Likely failure modes and fixes:

| Failure                                                          | Fix                                                                                                                                         |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `kubectl wait` times out on `kv/kubevirt`                        | Bump `--timeout` from `10m` to `15m`; emulation operator startup is slower than typical.                                                    |
| `helm/kind-action@v1` fails to start cluster                     | Pin a different `version:` (try `v0.23.0`); kind versions occasionally regress.                                                             |
| KubeVirt latest manifest references CRDs not in pinned kind      | Pin `KUBEVIRT_VERSION` explicitly (e.g., `export KUBEVIRT_VERSION=v1.4.0`) instead of resolving latest.                                     |
| VM never reaches `Running` (visible in failure-diagnostics step) | Inspect virt-launcher logs in the failure dump. Common: image pull from quay.io rate-limited — add a `kind load docker-image` preload step. |
| pytest reports `groups['molecule'] = []`                         | Re-check `_create_vm_dictionary.yml` retries; raise `retries: 10` to `retries: 20` if NodePort assignment is slow under emulation.          |
| ansible-lint fails on the workflow itself                        | ansible-lint doesn't lint workflow YAML; this would be yamllint or actionlint.                                                              |

For each iteration: edit, lint locally, commit, `git push`, watch CI again. Do **not** force-push; rebase merges keep history readable.

- [ ] **Step 5: Mark PR ready when CI is green**

```bash
gh pr ready
```

- [ ] **Step 6: Squash-merge after approval**

```bash
gh pr merge --squash --delete-branch
```

Expected: PR merged to `main`, branch deleted on remote, local `kubevirt-ci` orphaned (delete it with `git checkout main && git pull && git branch -D kubevirt-ci`).

---

## Self-Review

**Spec coverage check** (done while writing this plan):

| Spec section                                                                            | Implemented in                                                                                       |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Goal: regression coverage parity with podman                                            | Task 3 (kubevirt job + all_green wiring)                                                             |
| Non-goals (multi-platform, KVM, real-cluster CI, PVC, schema changes)                   | Honored — no tasks touch those                                                                       |
| Architecture (kind + KubeVirt + useEmulation + pytest -k kubevirt_ci)                   | Task 3                                                                                               |
| `extensions/molecule/kubevirt_ci/` scenario (all 6 files + group_vars)                  | Tasks 1, 2                                                                                           |
| Image: fedora-cloud-container-disk-demo, 1Gi mem, 5Gi disk                              | Task 2                                                                                               |
| `.github/workflows/tests.yml` job + all_green wiring                                    | Task 3                                                                                               |
| kind v0.24.0 pinned, KubeVirt latest at job time                                        | Task 3 Step 2                                                                                        |
| Failure diagnostics step                                                                | Task 3 Step 2 (Collect cluster diagnostics)                                                          |
| Test gating: existing conftest substring "kubevirt" still applies, no change            | Task 3 (uses `MOLECULE_KUBEVIRT_ENABLED=1` and `-k kubevirt_ci`) — confirmed no conftest edit needed |
| Documentation: CLAUDE.md command table row                                              | Task 5                                                                                               |
| Documentation: `docs/MIGRATION.md` no change                                            | Honored — no task touches it                                                                         |
| Documentation: `docs/examples/` no change                                               | Honored                                                                                              |
| Naming note: `kubevirt_ci` (underscore) for pytest, `kubevirt-ci` for kind cluster_name | Task 2 (scenario name), Task 3 (cluster_name)                                                        |

**Placeholder scan:** No "TBD", "TODO", or vague language remains. All YAML blocks are complete, all commands have expected output described.

**Type/name consistency:**

- Scenario directory: `extensions/molecule/kubevirt_ci/` — consistent across Tasks 1, 2, 5.
- Scenario name: `kubevirt_ci` — consistent in molecule.yml `scenario.name`, pytest selector, conftest substring match, branch name.
- Kind cluster_name: `kubevirt-ci` (hyphen) — consistent in Task 3 only.
- Branch name: `kubevirt-ci` (hyphen) — already created; consistent in commit/push commands.
- Image: `quay.io/kubevirt/fedora-cloud-container-disk-demo:latest` — consistent in Task 2 and the spec.
- KubeVirt CR field: `spec.configuration.developerConfiguration.useEmulation: true` — consistent in Task 3.
