# KubeVirt CI Test Job — Design

**Date:** 2026-05-08
**Status:** Approved
**Scope:** Add a GitHub Actions job that runs the kubevirt provisioner end-to-end on every PR, against an in-CI kind cluster with KubeVirt installed in software-emulation mode. Job is required for merge.

## Naming note

The new scenario is named `kubevirt_ci` (underscore, not hyphen). Pytest's `-k` expression parser treats hyphens as the subtraction operator: `-k kubevirt-ci` would parse as `kubevirt - ci` (subtraction of two identifiers). Underscore keeps the selector a valid Python identifier.

## Goal

Give the kubevirt provisioner the same level of automated regression coverage as the podman provisioner. Today, kubevirt only runs locally against a developer-provisioned cluster (gated by `MOLECULE_KUBEVIRT_ENABLED`). Provisioner regressions land on `main` undetected. We need CI that exercises the full lifecycle — `create` → `prepare` → `converge` → `verify` → `destroy` — on every change.

## Non-goals

- Multi-platform CI coverage (running both ubuntu and centos under emulation) — not worth the runtime cost under a must-pass job.
- KVM-accelerated CI on hosted runners — runner access to `/dev/kvm` is inconsistent across the GitHub free tier; not reliable enough for must-pass.
- Running the existing `extensions/molecule/kubevirt/` scenario in CI — it is a local-cluster fixture and remains so.
- Persistent storage (PVC, CDI). `containerDisk` is sufficient.
- Public schema changes. This work is purely additive: new molecule scenario + new CI job.

## Architecture

A new GitHub Actions job named `kubevirt` runs on `ubuntu-latest`. The job:

1. Checks out the collection into the canonical `ansible_collections/david_igou/molecule_provisioners` path.
2. Installs Python deps (ansible-core, molecule, pytest-ansible, kubernetes client) and Galaxy collection deps (containers.podman, kubernetes.core, community.crypto).
3. Provisions a single-node **kind** cluster via `helm/kind-action@v1`.
4. Installs the **KubeVirt operator** and **CR** by `kubectl apply`-ing the latest release manifests, then patches the CR to set `spec.configuration.developerConfiguration.useEmulation: true` so VMs run under QEMU TCG and do not require `/dev/kvm`.
5. Waits for `KubeVirt/kubevirt` to reach the `Available` condition.
6. Runs `pytest tests/integration -v -k kubevirt_ci` with `MOLECULE_KUBEVIRT_ENABLED=1`. The selector limits the run to the new CI scenario.
7. On failure, dumps cluster state (`get all -A`, `describe kv`, namespace describes, virt-launcher logs) for debugging.

The job is added to `all_green`'s `needs:` list and to its python result-set check, making it required for merge.

## New molecule scenario: `extensions/molecule/kubevirt_ci/`

Structurally identical to the existing `extensions/molecule/kubevirt/` (same role imports via FQCN dispatchers, same lifecycle one-liners). Differences are confined to `molecule.yml` and `group_vars/all.yml`.

### Why a separate scenario, not parameterization

The existing `kubevirt/` scenario targets developer-provisioned clusters with realistic ubuntu/centos images sized for hardware-accelerated VMs. Forcing it to also serve as CI fixture would either:

- Conflate local-cluster expectations with CI-image emulation tradeoffs, or
- Require runtime envsubst of platform fields, complicating the scenario for both audiences.

A second scenario keeps each fixture coherent. The cost is one extra `molecule.yml` + `group_vars/all.yml`; everything else (5 lifecycle one-liners) is essentially boilerplate.

### Image choice: `quay.io/kubevirt/fedora-cloud-container-disk-demo:latest`

- ~250MB containerdisk; image pull is one-time per runner.
- Full cloud-init support — our role's `users:` list with NOPASSWD sudo works as designed.
- Canonical KubeVirt-project demo image; well-tested under `useEmulation`.

Cirros was ruled out: ~12MB but its cloud-init dialect doesn't support the `users:` list shape the kubevirt role uses.

### Sizing

Single platform, `memory: 1Gi`, `disk_size: 5Gi`, `ssh_service.type: NodePort`, `ansible_user: cloud-user`, `namespace: ${MOLECULE_NAMESPACE:-molecule}`.

### Files

- `molecule.yml` — driver/platforms/provisioner/verifier/scenario blocks. `scenario.name: kubevirt_ci`. Single platform `fedora-emu`.
- `create.yml`, `destroy.yml`, `prepare.yml` — one-line `import_playbook: david_igou.molecule_provisioners.<phase>` each, with `name:` keys (per the ansible-lint 26.4+ memory).
- `converge.yml` — trivial `hosts: molecule`, no-op.
- `verify.yml` — `stat /usr/bin/sudo` + assert (mirrors the podman verify; proves cloud-init created the user with sudo).
- `group_vars/all.yml` — `ansible_connection: ssh`, `ansible_host_key_checking: false`, `ansible_ssh_private_key_file` pointing at the role-generated key path.

## CI workflow change: `.github/workflows/tests.yml`

New job:

```yaml
kubevirt:
  runs-on: ubuntu-latest
  timeout-minutes: 45
  steps:
    - uses: actions/checkout@v4
      with:
        path: ansible_collections/david_igou/molecule_provisioners
    - uses: actions/setup-python@v5
      with:
        python-version: "3.11"
    - name: Install python deps
      working-directory: ansible_collections/david_igou/molecule_provisioners
      run: |
        pip install ansible-core molecule "molecule-plugins[podman]" \
                    pytest pytest-ansible pytest-xdist kubernetes
    - name: Install collection dependencies
      working-directory: ansible_collections/david_igou/molecule_provisioners
      run: ansible-galaxy collection install containers.podman kubernetes.core community.crypto
    - uses: helm/kind-action@v1
      with:
        version: v0.24.0
        cluster_name: kubevirt-ci
        wait: 120s
    - name: Install KubeVirt operator + CR (emulation mode)
      run: |
        export KUBEVIRT_VERSION=$(curl -fsSL https://api.github.com/repos/kubevirt/kubevirt/releases/latest | jq -r .tag_name)
        kubectl apply -f "https://github.com/kubevirt/kubevirt/releases/download/${KUBEVIRT_VERSION}/kubevirt-operator.yaml"
        kubectl apply -f "https://github.com/kubevirt/kubevirt/releases/download/${KUBEVIRT_VERSION}/kubevirt-cr.yaml"
        kubectl -n kubevirt patch kubevirt kubevirt --type=merge \
          -p '{"spec":{"configuration":{"developerConfiguration":{"useEmulation":true}}}}'
        kubectl -n kubevirt wait --for=condition=Available kv/kubevirt --timeout=10m
    - name: Run kubevirt_ci scenario
      working-directory: ansible_collections/david_igou/molecule_provisioners
      env:
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

`all_green` updated:

- Add `kubevirt` to `needs:`.
- Add `'${{ needs.kubevirt.result }}',` to the python result-set check.

### Version pinning policy

- `kind` version pinned to `v0.24.0` so cluster bootstrap is deterministic.
- KubeVirt resolved at job time to the latest GitHub release. Rationale: KubeVirt's release cadence is roughly monthly and rolling-latest gives early signal on incompatibilities. If kubevirt-latest stops working with the pinned kind, the job fails and we bump kind explicitly.

## Test gating

`tests/integration/conftest.py` already skips items whose `nodeid` contains the substring `kubevirt` unless `MOLECULE_KUBEVIRT_ENABLED` is truthy. The new scenario `kubevirt_ci` matches that substring, so it is correctly gated by the same env var. **No conftest change is required.**

CI sets `MOLECULE_KUBEVIRT_ENABLED=1` and uses `-k kubevirt_ci` to run only the new scenario, leaving the original `kubevirt/` scenario excluded from CI.

A developer running `pytest tests/integration -v` locally with `MOLECULE_KUBEVIRT_ENABLED=1` will get **both** scenarios. They can scope to one with `-k kubevirt_ci` (CI fixture) or `-k kubevirt and not ci` (local-cluster scenario).

## Runtime budget

Empirical estimates from KubeVirt project CI under emulation:

- KubeVirt operator → Available: 2-4 min.
- Single fedora VM boot under TCG: 3-7 min (dominated by cloud-init).
- Containerdisk first pull (~250MB): <1 min on hosted runners.
- Ansible playbook overhead (prepare + converge + verify + destroy): 2-3 min.

**Total expected: 12-20 min.** `timeout-minutes: 45` gives ~2x headroom for variance.

## Failure modes and mitigations

- **`/dev/kvm` absent** → `useEmulation: true` patched into the CR.
- **VM stuck pending** → diagnostics step dumps `kubectl describe vmi` and virt-launcher logs.
- **NodePort timing race** → already mitigated in `roles/kubevirt/tasks/_create_vm_dictionary.yml` (until/retries when reading `spec.ports[0].nodePort` back).
- **kind networking** → default CNI is sufficient; the role's `masquerade: {}` interface mode works without flannel.
- **kubevirt-latest breaks against pinned kind** → manifests as job failure; bump kind in the workflow.
- **quay.io rate-limit** → not anticipated; can add a containerdisk preload step in a follow-up if it bites.
- **Reboot-required failures** (kubevirt#6885) → not encountered: our scenario does not reboot VMs.

## Documentation changes

- `CLAUDE.md` command table: add `Run kubevirt_ci self-test (CI fixture)` row pointing at `pytest -k kubevirt_ci`.
- `docs/MIGRATION.md`: no change. Consumers do not see the CI scenario.
- `docs/examples/`: no change. The CI scenario is a self-test fixture, not public API.

## Out-of-scope follow-ups (not blocking this work)

- Containerdisk preload via kind image-load step if registry pulls become flaky.
- Caching the KubeVirt operator manifests across runs.
- A separate "real-cluster" CI matrix triggered on schedule (would require infra outside hosted runners).
