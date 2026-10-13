# Local QEMU consumer fixture

Install the built collection into an isolated collection directory and point
`ANSIBLE_COLLECTIONS_PATH` at it, followed by the controller's dependencies.
From this directory, run `molecule create`, `molecule prepare`, `molecule list`,
`molecule login --host instance`, and `molecule test`. Destroy the fixture when
finished. Override `MP_TEST_QEMU_IMAGE` with a pinned Debian cloud-image URL and
`MP_TEST_QEMU_CACHE` with the shared controller image cache. This fixture tests
public dispatcher imports and runtime inventory from a separate consumer.
