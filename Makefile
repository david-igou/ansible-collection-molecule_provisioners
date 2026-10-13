SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

# Local tests use .build/ for the collection layout. Preserve pytest-ansible's
# collections/ tree, installed dependencies, and caller-supplied search paths.
export ANSIBLE_COLLECTIONS_PATH := $(CURDIR)/.build:$(CURDIR)/collections:$(HOME)/.ansible/collections:$(ANSIBLE_COLLECTIONS_PATH)

CANONICAL := $(CURDIR)/.build/ansible_collections/david_igou/molecule_provisioners

SCENARIO_DIR := extensions/molecule/default

.PHONY: help install lint ee test-aws \
        test test-podman test-kubevirt test-docker test-qemu \
        test-unit test-podman-startup test-kubevirt-lifecycle test-kubevirt-access \
        test-kubevirt-storage test-kubevirt-kind test-qemu-functional test-docker-functional \
        podman kubevirt docker qemu \
        test-qemu-testing sanity build pre-commit clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

AWS_TEST_IMAGE ?= localhost/mp-aws-ee:dev

ee: ## Build the local AWS test execution environment
	podman build -f tests/ee/Containerfile -t $(AWS_TEST_IMAGE) .

test-aws: $(CANONICAL) ## Opt-in live AWS lifecycle tests inside the local EE
	podman run --rm --network host --cgroups=disabled \
	  -v $(CURDIR):$(CURDIR):Z -w $(CURDIR) \
	  -v $(HOME)/.ansible/collections:/opt/ansible/collections:ro \
	  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
	  -e AWS_REGION -e AWS_DEFAULT_REGION -e MP_TEST_AWS_ACCOUNT_ID \
	  -e MP_TEST_AWS_CIDR -e RUN_AWS_LIFECYCLE=1 \
	  -e ANSIBLE_COLLECTIONS_PATH=$(CURDIR)/.build:$(CURDIR)/collections:/opt/ansible/collections \
	  -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(AWS_TEST_IMAGE) \
	  pytest tests/integration/aws -v -o addopts="" $(AWS_TEST_ARGS)

install: ## Install python + ansible collection dependencies
	python -m pip install --upgrade pip
	pip install -r lint-requirements.txt -r requirements.txt -r test-requirements.txt
	ansible-galaxy collection install containers.podman kubernetes.core community.crypto community.docker community.general ansible.netcommon community.routeros \
	  'amazon.aws:>=9.0.0,<12.0.0' 'community.aws:>=9.0.0,<12.0.0' 'ansible.windows:>=2.5.0,<4.0.0'

lint: ## Lint Ansible, YAML and changelog fragments
	ansible-lint
	yamllint .
	antsibull-changelog lint

test-unit: $(CANONICAL) ## Offline renderer and validation tests; no backend required
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/unit -q -o addopts=""

test-podman-startup: $(CANONICAL) ## Container startup and cleanup regressions; needs Podman
	RUN_PODMAN_STARTUP=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/integration/podman -v -o addopts=""

test-kubevirt-lifecycle: $(CANONICAL) ## Isolation, full definitions and failed cleanup; needs KUBECONFIG
	@test -n "$${KUBECONFIG:-}" || { echo 'Set KUBECONFIG to the test cluster.' >&2; exit 1; }
	RUN_KUBEVIRT_ISOLATION=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/integration/kubevirt/test_definition.py tests/integration/kubevirt/test_run_isolation.py -v -o addopts=""

test-kubevirt-access: $(CANONICAL) ## Custom SSH and TCP/UDP application access; needs KUBECONFIG
	@test -n "$${KUBECONFIG:-}" || { echo 'Set KUBECONFIG to the test cluster.' >&2; exit 1; }
	RUN_KUBEVIRT_ISOLATION=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/integration/kubevirt/test_access.py -v -o addopts=""

test-kubevirt-storage: $(CANONICAL) ## Managed-disk I/O and cleanup; needs KUBECONFIG and CDI
	@test -n "$${KUBECONFIG:-}" || { echo 'Set KUBECONFIG to the test cluster.' >&2; exit 1; }
	RUN_KUBEVIRT_STORAGE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/integration/kubevirt/test_storage.py -v -o addopts=""

test-kubevirt-kind: ## Create a disposable local KubeVirt/CDI cluster and run all KubeVirt tests
	bash scripts/test-kubevirt-kind.sh

test-qemu-functional: $(CANONICAL) ## QEMU validation and CPU launch checks; needs QEMU tools
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/integration/qemu/test_qemu_unit.py -m 'not slow' -v -o addopts=""

test-qemu-testing: $(CANONICAL) ## Local QEMU guest I/O, routing, forwards, isolation and diagnostics
	RUN_QEMU_TESTING=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/integration/qemu/test_testing_features.py tests/integration/qemu/test_consumer.py -v -o addopts=""

test-docker-functional: $(CANONICAL) ## Docker role regressions; needs a reachable Docker daemon
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/integration/docker/test_docker_unit.py -v -o addopts=""

test: test-podman ## Alias for test-podman (the kubevirt backend needs a live cluster)

test-podman: ## pytest-driven podman self-test (tests/integration -k default)
	PROVISIONER=podman pytest tests/integration -v -k default

test-kubevirt: ## pytest-driven kubevirt self-test (needs KUBECONFIG with KubeVirt)
	PROVISIONER=kubevirt pytest tests/integration -v -k default -s -o addopts=""

test-docker: ## pytest-driven docker self-test
	PROVISIONER=docker pytest tests/integration -v -k default

test-qemu: ## pytest-driven qemu self-test (needs QEMU + cloud-localds or genisoimage)
	PROVISIONER=qemu pytest tests/integration -v -k default

podman: ## `molecule test` against podman directly (bypasses pytest)
	cd $(SCENARIO_DIR) && PROVISIONER=podman molecule test

kubevirt: ## `molecule test` against kubevirt directly (needs KUBECONFIG with KubeVirt)
	cd $(SCENARIO_DIR) && PROVISIONER=kubevirt molecule test

docker: ## `molecule test` against docker directly
	cd $(SCENARIO_DIR) && PROVISIONER=docker molecule test

qemu: ## `molecule test` against qemu directly (needs QEMU + cloud-localds or genisoimage)
	cd $(SCENARIO_DIR) && PROVISIONER=qemu molecule test

sanity: $(CANONICAL) ## Run ansible-test sanity inside a Docker container
	cd $(CANONICAL) && ansible-test sanity --docker $(SANITY_ARGS)

build: ## Build the collection artifact
	ansible-galaxy collection build --force

pre-commit: ## Run pre-commit on all files
	pre-commit run --all-files

clean: ## Remove build artifacts and pytest-ansible's symlinked collection tree
	rm -rf .build/ collections/ *.tar.gz

# ansible-test needs cwd to *resolve* (via realpath) to a directory inside an
# ansible_collections/<ns>/<name>/ tree. A plain symlink doesn't work because
# ansible-test follows it back to the repo root. Materialize per-item symlinks
# instead, matching what pytest-ansible's molecule_scenario fixture builds.
$(CANONICAL):
	@mkdir -p $(CANONICAL)
	@for item in $$(ls -A $(CURDIR) | grep -v '^\.build$$'); do \
	  ln -sfn $(CURDIR)/$$item $(CANONICAL)/$$item; \
	done
