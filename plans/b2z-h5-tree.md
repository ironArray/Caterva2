# Hierarchical containers in Caterva2 (`.b2z` TreeStore, and `.h5`)

Design notes + plan for browsing/querying hierarchical containers via **virtual
descent** (address inner members by path, no unfolding to disk).

## Background

A `.b2z` may hold a **TreeStore** (a hierarchy of NDArray/CTable leaves), not
just a single `CTable`. Rather than the old HDF5 `unfold` approach (walk the
container and write proxy files replicating the hierarchy), we address leaves by
virtual path, e.g. `@personal/tree-store.b2z/level1/ctable`, resolving on the fly.

Key asymmetry: TreeStore leaves are already native blosc2 objects
(`tree[key]` → NDArray/CTable), so the seam is cheap. HDF5 leaves are not, so
they must be wrapped in an `HDF5Proxy` — which today writes a `.b2nd` proxy file
(the unfold path). Virtual descent for `.h5` needs a *file-less* proxy.

## Done (branch `new-table`) — `.b2z` TreeStore

Implemented and tested (`caterva2/tests/test_treestore.py`), full suite green.

Core seam (`srv_utils.py`):
- `split_container_path(path)` → `(container_path, inner_key)`, split at the
  `.b2z` boundary. `BLOSC2_CONTAINER_SUFFIXES = {".b2z"}` is the knob.
- `treestore_leaves(tree, prefix)` → deep leaf keys (groups skipped).
- `treestore_size(tree, prefix)` → on-disk size summed from the `.b2z` zip index
  (`_get_zip_offsets`, guarded), no per-leaf open.
- `read_metadata(obj, mtime=None)` — TreeStore container → `models.Directory`;
  accepts an mtime for opened leaf objects (leaves inherit the container mtime).

API (`server.py`): `get_list` descends; `get_info`/`fetch_data` open
`tree[inner_key]` (leaf → metadata/slice; group → `Directory`); `fetch` guards
the whole-file `FileResponse` fast path so it never streams the whole container.

Web (`server.py`): `htmx_path_list` expands a container into one row per leaf;
`htmx_path_info`/`htmx_path_view` split + open the leaf.

Groups unified via `models.Directory{kind:"dir", mtime, size, nfiles}`:
`get_info` returns it for a real directory, a TreeStore container (root group),
and a virtual group inside one. Size is cheap (zip index for `.b2z` groups;
aggregate file stat for real dirs).

Client (`client.py`): new `Group` class (browsable/indexable; exported from
`caterva2`). `Root.__getitem__` dispatches on server `kind`
(dir→`Group`, ctable→`Table`, shape→`Array`, else `File`) for any
non-`.b2nd`/`.b2frame` path; `File`/`Array` accept `meta=` to avoid a double
`/api/info` round-trip.

## Open questions

1. **Route `.h5` through the same seam** (chosen direction; plan below). Would
   let us eventually retire the `unfold` proxy-file path.
2. **Web cosmetics** (minor, marked with `ponytail:` comments):
   - Each expanded leaf row in the web tree shows the *container's* size
     (`add_dataset` stats the container file). Would need per-leaf size.
   - Filter/sort on a TreeStore leaf falls back to unfiltered (default view is
     fine).
3. **Client perf tradeoff** (accepted, not a bug): `Root.__getitem__` now does
   one `/api/info` call for any non-`.b2nd`/`.b2frame` path (dirs, `.b2z`, plain
   files). Deduped via `meta=`, but still one round-trip where plain files used
   to be lazy. Revisit only if a hot path does many `root['plainfile']` lookups.
4. **Not committed** — all changes are in the working tree on `new-table`.

## Plan: route `.h5` through the seam (~70–90 lines, mostly `hdf5.py`)

More than `.b2z` (which was ~1 helper) because HDF5 isn't native, but bounded —
the heavy lifting (HDF5Proxy, chunk readers, `to_cframe`) already exists from the
unfold work. Feasibility of the two hard parts verified with a scratch h5 file:
`visititems` enumerates leaves cheaply, `dset.id.get_storage_size()` gives
per-dataset size, and `blosc2.empty(shape, dtype, **b2args)` with no urlpath
yields an in-memory b2arr carrying cparams.

1. **File-less proxy** (~15 lines, `hdf5.py`) — factory `HDF5Proxy.open(h5file,
   dsetname)` that sets `self.dset` to the live h5 dataset and builds an
   in-memory `self.b2arr = blosc2.empty(shape, dtype, **b2args_from_h5dset(...))`
   (no urlpath → no disk write). Reuses existing `slice`/`to_cframe`/properties
   (`slice` uses `self.dset` + `self.b2arr.cparams`, both satisfied). This is the
   one genuinely new mechanism.
2. **Enumerate + size helpers** (~12 lines, `hdf5.py`) — `hdf5_leaves(path,
   prefix)` via `visititems`; `hdf5_size(path, prefix)` via
   `dset.id.get_storage_size()`. Analogs of `treestore_leaves`/`treestore_size`.
3. **`read_metadata`** (~8 lines) — plain `.h5` (no inner key) → `Directory`
   (currently `File`) with nfiles/size; add an `HDF5Proxy` branch for a leaf
   object.
4. **Server descent** (~25 lines) — `get_list`/`get_info`/`fetch_data` currently
   hardcode `.b2z` + `blosc2.open` + TreeStore. Add `.h5` siblings; cleanest is a
   tiny "container adapter" (`open → is-group? Directory : leaf-proxy`) so both
   formats share the flow.
5. **`split_container_path`** — add `.h5`, `.hdf5` to
   `BLOSC2_CONTAINER_SUFFIXES` (1 line; `get_abspath` already permits `.h5`).
6. **Web** (~8 lines) — generalize the path-list leaf-row expansion to `.h5`.
7. **Client** — zero change; already dispatches on server `kind`.

### Decisions / risks
- **Keep `unfold` intact** in this change; virtual descent and unfold both target
  the same `.h5` differently. Retiring unfold (drop the command +
  `create_hdf5_proxies` + the proxy-file reconstruct mode of `HDF5Proxy`) is a
  clean follow-up once this proves out.
- **Compat**: `read_metadata` on a plain `.h5` flips `File`→`Directory`; a couple
  of `test_hdf5_proxy` assertions may expect `File` — check/adjust.
- **Incompatible leaves** (compound/vlen/scalar h5 datasets):
  `h5dset_is_compatible` exists; skip or surface them in enumeration rather than
  crash.

### Suggested increments
- Spike: file-less proxy (#1) + enumeration (#2) alone, verified with a test.
- Then wire server descent (#3–#5), then web (#6).
