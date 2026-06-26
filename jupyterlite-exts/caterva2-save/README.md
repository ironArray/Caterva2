# caterva2-save

A small JupyterLab/JupyterLite frontend extension that mirrors notebook **saves**
back to the caterva2 server's `api/upload/<path>` endpoint.

Vanilla JupyterLite only persists saves to the browser's local storage, so
without this plugin edits never reach the caterva2 server. This replaces the one
caterva2-specific patch the `ironArray/jupyterlite` fork carried (`c2upload` in
`packages/contents/src/drive.ts`), letting the deployment build JupyterLite from
**stock upstream** packages instead of a fork.

It is shipped *inside the caterva2 wheel* (as a federated labextension under
`share/jupyter/labextensions/caterva2-save`), so `jupyter lite build` discovers
and bundles it automatically — no separate package or repo.

## Build (needs Node + the JupyterLab build toolchain)

From the repo root:

```bash
make lite-ext              # jlpm install && jlpm build:prod  -> ./labextension/
```

Commit the resulting `labextension/` directory (like
`caterva2/services/static/build/`). Downstream installs then need **no Node**:
the `pyproject.toml` build hook uses `skip-if-exists`, so a committed
`labextension/` makes the wheel build a pure-Python copy.

## Develop / test locally

The easiest path is `make lite-ext` from the repo root (see the `Makefile`): it
runs `jlpm install && jlpm build:prod`, then symlinks the built `labextension/`
into the environment's `share/jupyter/labextensions/` — needed because an
*editable* caterva2 install does not stage the wheel's `shared-data` there (a
real wheel install does).

> Build with **`build:prod`**, not `jlpm build` (which is the *dev* build:
> non-minified, source-mapped, different chunk names). The committed,
> wheel-shipped `labextension/` must be the prod bundle.

> Note: `jupyter labextension develop` does **not** work here — it expects the
> extension to be its own Python package (`setup.py`/`pyproject`), but this one
> is shipped by the `caterva2` package. Use the symlink (as `make lite-ext`
> does) instead.

Then rebuild the lite site and check it is bundled:

```bash
make lite-build BIN="$(dirname $(which jupyter))"
jupyter labextension list          # should list caterva2-save
```

## Verify it works

Open a served notebook, edit and save it, and confirm the caterva2 server log
shows a `POST /api/upload/<path>` (and that the change survives in a different
browser / after clearing site data).
