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

| Pyodide | Emscripten / ABI    | bundled micropip | PEP 783 |
|---------|---------------------|------------------|---------|
| 0.29.0  | 4.0.9 / `2025_0`    | 0.11.0           | no      |
| 0.29.3  | 4.0.9 / `2025_0`    | 0.11.0           | no      |
| 0.29.4  | 4.0.9 / `2025_0`    | 0.11.1           | **yes** |
| 314.0.0 | (Python 3.14 line)  | 0.11.1           | **yes** |

jupyterlite-pyodide-kernel defaults:

| kernel  | default Pyodide |
|---------|-----------------|
| 0.7.0 (our pin) | 0.29.0 (via CDN `pyodideUrl` default) |
| 0.7.1   | 0.29.3          |
| 0.8.0b0 | 0.29.4          |

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

## Stage 2 — when jupyterlite-pyodide-kernel 0.8.0 goes final

(0.8.0b0, 2026-05-25, already bundles Pyodide 0.29.4; wait for the final
release.)

1. In `pyproject.toml` (`server` extra), bump:
   - `jupyterlite-core[contents]==0.7.1` → latest 0.8.x
   - `jupyterlite-pyodide-kernel==0.7.0` → 0.8.x
   (the two must be bumped together; check the kernel's release notes for the
   matching core version).
2. Delete the `pyodideUrl` override from `jupyter-lite.json` (the kernel's
   default Pyodide is then >= 0.29.4).  Keep the file if other settings have
   accumulated in it.
3. `make lite-build`, smoke-test (see below), redeploy.
4. Keep the micropip self-heal in the bootstrap cell for a transition period:
   it is a no-op on up-to-date runtimes and still rescues third-party
   deployments running older caterva2 static sites.

## Stage 3 — Pyodide 314.x line (later, separate effort)

Pyodide moved to a new versioning scheme: 314.0.0 (2026-06-09) tracks
**Python 3.14** and is PEP 783-native; cibuildwheel 4.0 builds against
314.0.0a2.  This is *not* a drop-in:

- Wait for a jupyterlite-pyodide-kernel release that officially supports the
  314.x line; do not just point `pyodideUrl` at it under a 0.7/0.8 kernel
  (different Python minor, different ABI, piplite wheels must match).
- blosc2 wasm wheels must be built for the matching ABI: bump
  `CIBW_PYODIDE_VERSION` in python-blosc2's `.github/workflows/wasm.yml`
  (currently 0.29.3) in lockstep, and keep cp313 vs cp314 in mind
  (`CIBW_BUILD` is currently `cp313-*`).
- Re-check any packages the notebooks load from the Pyodide distribution
  (numpy, matplotlib, …) exist in the 314.x lockfile.

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
