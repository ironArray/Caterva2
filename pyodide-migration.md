# Pyodide / PEP 783 migration plan

*Written 2026-06-10.  Context: blosc2 4.4.3 wasm32 wheels started failing to
install in our JupyterLite deployments.*

## Background

PEP 783 introduces a new platform tag for Pyodide wheels, `pyemscripten`
(e.g. `pyemscripten_2025_0_wasm32`), replacing the older
`emscripten_X_Y_Z_wasm32` and `pyodide_YYYY_N` spellings.  cibuildwheel 4.0
(June 2026) emits the new tag by default, which is how python-blosc2 4.4.3
wasm32 wheels got it.  Installing such a wheel requires **micropip >= 0.11.1**
(2026-04-02); older micropip mis-parses the tag and fails with:

    ValueError: Wheel was built with Emscripten vpyemscripten.2025.0 but
    Pyodide was built with Emscripten v4.0.9

Relevant version matrix (verified against the pyodide-lock.json of each CDN
distribution):

| Pyodide | Emscripten / ABI       | bundled micropip | PEP 783 |
|---------|------------------------|------------------|---------|
| 0.29.0  | 4.0.9 / `2025_0`       | 0.11.0           | no      |
| 0.29.3  | 4.0.9 / `2025_0`       | 0.11.0           | no      |
| 0.29.4  | 4.0.9 / `2025_0`       | 0.11.1           | **yes** |
| 314.0.0 | 5.0.3 / `2026_0` (Py 3.14) | 0.11.1       | **yes** |

jupyterlite-pyodide-kernel defaults:

| kernel  | default Pyodide |
|---------|-----------------|
| 0.7.0   | 0.29.0 (via CDN `pyodideUrl` default) |
| 0.7.1   | 0.29.3          |
| 0.8.0b0 | 0.29.4          |
| 0.8.0 (final, our pin) | **314.0.0** (Python 3.14 — *not* 0.29.4; see Stage 3) |

## Stage 1 — done (2026-06-10)

Goal: accept `pyemscripten` wheels without upgrading the JupyterLite stack.

1. **`jupyter-lite.json`** (repo root, picked up by `make lite-build`):
   overrides `pyodideUrl` of `@jupyterlite/pyodide-kernel-extension:kernel`
   to the CDN Pyodide **v0.29.4**.  Same ABI (`2025_0`, Emscripten 4.0.9) as
   the 0.29.0 default, so it is a drop-in for kernel 0.7.x, but it bundles
   micropip 0.11.1.  Requires `make lite-build` + redeploy to take effect.

2. **`caterva2/services/server.py`** (`PYODIDE_BOOTSTRAP_CELL_SOURCE`): the
   injected bootstrap cell now self-upgrades micropip to >= 0.11.1 (and
   re-imports it) before installing the blosc2/caterva2 wheels.  Because the
   cell is injected at serve time, this also fixes deployments whose static
   lite site has not been rebuilt yet.

## Stage 2 — superseded by Stage 3 (see below)

This stage assumed `jupyterlite-pyodide-kernel` 0.8.0 would default to Pyodide
**0.29.4** (as 0.8.0b0 did), making it a safe drop-in for the 0.29.x ABI.  That
assumption proved **wrong**: the *final* 0.8.0 release (the one we pinned)
defaults to Pyodide **314.0.0** instead — Python 3.14, PEP 783-native, the
314.x line that this document originally filed under Stage 3.  So bumping the
kernel to 0.8.0 *is* the jump to Stage 3; there is no intermediate "0.8.0 on
0.29.4" step.  The original Stage 2 instructions are kept here only for the
record:

1. ~~Bump `jupyterlite-core[contents]` and `jupyterlite-pyodide-kernel` from
   0.7.x to 0.8.x in `pyproject.toml` (`server` extra), together.~~ Done — both
   pinned to `==0.8.0`.
2. ~~Delete the `pyodideUrl` override from `jupyter-lite.json` (kernel default
   is then >= 0.29.4).~~ Done — the file held only that override, so it was
   removed wholesale (commit `7d89a3f`).  But note the default is 314.0.0, not
   0.29.4 (see Stage 3).

To have *stayed* on the 0.29.x ABI we would have had to either keep the 0.7.x
kernel, or re-add a `jupyter-lite.json` pinning `pyodideUrl` back to v0.29.4
under the 0.8.0 kernel.  We chose not to, because the 314.x pieces were already
in place (next section).

## Stage 3 — Pyodide 314.x line — done (2026-06-25)

Pyodide 314.0.0 (2026-06-09) tracks **Python 3.14** and is PEP 783-native; it is
ESM-only (loaded from `pyodide.mjs`).  `jupyterlite-pyodide-kernel` 0.8.0
defaults to it (verified in the built site:
`pyodideUrl = https://cdn.jsdelivr.net/pyodide/v314.0.0/full/pyodide.mjs`).

Why migrating straight here was feasible — the runtime ABI and all blosc2
dependencies line up (verified against the v314.0.0 `pyodide-lock.json`):

| Pyodide 314.0.0          | blosc2 cp314 wheel               |
|--------------------------|----------------------------------|
| python 3.14.0            | `cp314`                          |
| abi `2026_0`             | `pyemscripten_2026_0`            |
| platform `emscripten_5_0_3` | (matches)                     |

- blosc2 **cp314** wasm wheel exists:
  `blosc2-4.5.1-cp314-cp314-pyemscripten_2026_0_wasm32.whl`, published **on
  PyPI** (the GitHub `wheels` branch still hosts only the cp313/0.29.x wheel).
- blosc2 runtime deps are all satisfiable on 314.0.0: numpy 2.4.3, ndindex,
  msgpack, pydantic + pydantic-core, requests are in the lockfile; `numexpr`
  and `threadpoolctl` are correctly excluded on `wasm32`; `rich`/`textual` are
  pure-Python (micropip → PyPI).  No `py-cpuinfo` requirement.
- caterva2 is pure-Python (`py3-none-any`), installs on any runtime.
- micropip 0.11.1 is bundled, so no micropip self-heal is needed in the
  bootstrap cell (the Stage-1 upgrade shim was removed).

What was done:

1. `pyproject.toml`: `jupyterlite-core[contents]==0.8.0`,
   `jupyterlite-pyodide-kernel==0.8.0` (see Stage 2).
2. `jupyter-lite.json` override removed (see Stage 2) — we now ride the kernel's
   314.0.0 default.
3. Bootstrap cell (`caterva2/services/notebook.py`,
   `PYODIDE_BOOTSTRAP_CELL_SOURCE`): install **by name from PyPI** rather than
   by GitHub wheel URL, so micropip picks the wheel matching the running ABI:
   `await micropip.install(["blosc2>=4.6.0", "caterva2"])`.  This replaces the
   old `latest.txt` + URL construction.  The Stage-1 micropip self-heal (upgrade
   to >= 0.11.1) was dropped once we settled on the 314.x line, which already
   bundles micropip 0.11.1 — the branch could never fire.

   **The `blosc2>=4.6.0` floor is load-bearing.** Pyodide ships its *own*
   blosc2 in the distribution lock — e.g. 314.0.0 bundles
   `blosc2-4.1.2-cp314-cp314-pyemscripten_2026_0_wasm32.whl` — and micropip
   resolves a bundled/locked package **before** PyPI when the requirement has no
   constraint that excludes it.  So a bare `micropip.install("blosc2")` silently
   installs the stale bundled 4.1.2 instead of the latest release.  The `>=`
   floor (above any version Pyodide is likely to bundle, on a release that has
   cp314 wheels on PyPI) forces micropip to fetch a current release from
   PyPI.  caterva2 is not in the lock, so it already comes from PyPI.  Bump the
   floor when you need to require something newer.  To *always* pull the
   absolute latest regardless of what Pyodide bundles you would need a direct
   wheel URL (the old `latest.txt` model), which needs cp314 wheels on the
   GitHub `wheels` branch — see the dev-vs-release caveat below.
4. JupyterLite contents loading: 0.8 gates the server-directory fetch on the
   `contentsAllJsonFile` config option, which the build only sets when it
   indexes local `files/` (we index none — caterva2 serves contents
   dynamically).  Re-added a repo-root `jupyter-lite.json` setting
   `jupyter-config-data.contentsAllJsonFile = "all.json"` so the browser drive
   fetches `api/contents/<dir>/all.json` + `files/<path>` from the caterva2
   server again.  No fork needed for loading (the fork only ever patched
   *save*-back).
5. JupyterLite save-back: re-implemented the fork's `c2upload` patch as a small
   standalone labextension, `jupyterlite-exts/caterva2-save/`, that wraps the
   contents-manager `save()` to `POST` the notebook to `api/upload/<path>`
   (`credentials: same-origin` reuses the caterva2 session cookie).  It is
   shipped *inside the caterva2 wheel* as a federated extension
   (`share/jupyter/labextensions/caterva2-save`, via `pyproject.toml`
   `shared-data` + a `hatch-jupyter-builder` hook with `skip-if-exists`), so
   `jupyter lite build` discovers and bundles it with no extra package.  Build
   it with `make lite-ext` (Node + jlpm) and commit the resulting
   `labextension/`; downstream installs then need no Node.
6. `make lite-build` succeeds with the 0.8.0 stack.

**The `ironArray/jupyterlite` fork is fully retired.**  Every piece now comes
from stock upstream: kernel/Pyodide (`jupyterlite-pyodide-kernel==0.8.0`),
loading (server endpoints + `contentsAllJsonFile`), saving (the bundled
`caterva2-save` extension), and the build (`jupyter lite build`).  In
`caterva2-deploy`, the `make lite` target's
`gh run -R ironArray/jupyterlite download` step can be dropped — just install
caterva2 (which carries the extension) and build.

Caveats / follow-ups:

- **Dev vs. release wheels.** Installing by name pulls PyPI *releases*; the old
  GitHub-URL path pulled the latest *CI/dev* wheel.  To get dev wheels in the
  browser again, publish cp314 wheels to python-blosc2's `wheels` branch: add
  `cp314` to `CIBW_BUILD` (currently `cp313-*`) and bump `CIBW_PYODIDE_VERSION`
  (currently 0.29.3) to the 314.x line in `.github/workflows/wasm.yml`.
- **Browser smoke test passed** (2026-06-25): a served notebook on Pyodide
  314.0.0 runs the bootstrap cell and reports
  `Installed blosc2 4.5.1 and caterva2 2025.12.3 successfully!` — confirming the
  cp314/`pyemscripten_2026_0` wheel installs and the `>=4.5.1` floor correctly
  overrides the bundled 4.1.2.
- **Save-back verified** (2026-06-26): editing and saving a served notebook
  produces `POST /api/upload/<path> 200` on the caterva2 server — the bundled
  `caterva2-save` extension round-trips edits to the server, matching the old
  fork behaviour with no fork.

## Smoke test

After any of the stages:

1. `make lite-build` and serve the site (or use a deployed instance).
2. Open a notebook; in a Python (Pyodide) kernel run:

   ```python
   import micropip, pyodide

   print(pyodide.__version__, micropip.__version__)  # expect >= 0.29.4 / >= 0.11.1
   ```

3. Run the auto-injected bootstrap cell (first cell of any served notebook)
   and verify the latest blosc2 wasm32 wheel (a `pyemscripten_*` tag since
   4.4.3) installs and `import blosc2` works.

## Related: python-blosc2 side

- `wasm.yml` there installs cibuildwheel unpinned; the 3.4 → 4.0 jump is what
  switched the wheel tag (the binary ABI did not change).  Pin it, or keep
  4.x and accept the new tag (which this migration enables).
- For already-deployed JupyterLite sites that cannot be updated, a published
  wheel can be re-tagged to the legacy spelling with
  `wheel tags --platform-tag emscripten_4_0_9_wasm32 <wheel>`.

Operational note: change 1 only reaches users after you run make lite-build and redeploy, while change 2 fixes any notebook served by an
updated caterva2 server immediately — so even deploying just the server-side change unblocks the blosc2 4.4.3 installs.
