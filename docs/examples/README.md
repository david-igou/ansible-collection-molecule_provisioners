# Example scenario

The files from the
[Getting started guide](https://david-igou.github.io/ansible-collection-molecule_provisioners/docsite/guide_getting_started.html),
ready to copy into a collection:

```text
docs/examples/                 → copy into your collection root as:
├── config.yml                 → extensions/molecule/config.yml
├── requirements-test.yml      → extensions/molecule/requirements-test.yml
├── default/                   → extensions/molecule/default/
├── Makefile                   → Makefile (optional: sets MOLECULE_GLOB)
└── ansible.cfg                → ansible.cfg (optional)
```

From your collection root (the directory with `galaxy.yml`):

```console
$ git clone --depth 1 https://github.com/david-igou/ansible-collection-molecule_provisioners.git /tmp/mp
$ mkdir -p extensions/molecule
$ cp -r /tmp/mp/docs/examples/{config.yml,requirements-test.yml,default} extensions/molecule/
$ cp /tmp/mp/docs/examples/Makefile .
$ make test
```
