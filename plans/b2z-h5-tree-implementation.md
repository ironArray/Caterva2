# Implementation plan: browse `.h5` files as mountable virtual roots

Status: implemented on `new-table` (all 6 steps + client compat fixes below).
Companion to `plans/b2z-h5-tree.md` (design) and `plans/b2z-virtual-roots.md`
(the shipped `.b2z` web feature this extends).

Deviation from plan: the `read_metadata` File→Directory flip for a plain
`.h5` (Step 3) broke more than "a few test_hdf5_proxy assertions" — `Root.__getitem__`
now returns a client `Group` instead of `File` for `.h5` uploads, so generic
`File` operations (`unfold`, `copy`, `move`, `remove`, `download`) needed
thin delegating equivalents added to `client.Group` (`caterva2/client.py`).
Container-adapter `close()` timing also turned out not to need the `with`
wrapping the plan suggested: TreeStore leaves survive `close()`, and an
HDF5 leaf's `h5py.File` only needs to stay open until `container`/`arr` go
out of scope (CPython refcounting), which happens naturally at the end of
each handler — so `fetch_data`/`htmx_path_view` simply never close explicitly
for leaf reads (documented inline).

## Context

`.b2z` TreeStore containers can already be browsed by **virtual descent**
(address inner leaves by path, no unfolding to disk) in both the API and the
web UI. The web UI shipped a **virtual-roots** model: a container shows as a
single mountable row, the user mounts it (client `localStorage`) to get a
virtual root whose leaves are then listed.

We want `.h5`/`.hdf5` files to browse **the same way** — one row, mount, expand
leaves — retiring nothing (the legacy `unfold` proxy-file path stays intact for
now). The blocker is that today the descent code is hardcoded to `.b2z` +
`blosc2.open` + `blosc2.TreeStore` in ~7 places (API: `get_list`, `get_info`,
`fetch_data`; web: the two `htmx_path_list` loops, `htmx_path_info`,
`htmx_path_view`), and HDF5 leaves aren't native blosc2 objects so they need a
*file-less* `HDF5Proxy`.

## The pivot: one container adapter

Introduce a tiny adapter in `srv_utils.py` that gives `.b2z` and `.h5` a common
shape, so every descent call site stops branching on suffix. Two
implementations behind one interface (justified: not a one-impl abstraction —
it is the seam that removes ~7 scattered `if suffix == ".b2z"` /
`blosc2.open(abspath)[inner_key]` blocks and is what makes the web generic).

```python
# srv_utils.py
BLOSC2_CONTAINER_SUFFIXES = {".b2z", ".h5", ".hdf5"}  # was {".b2z"}


def open_container(abspath):
    """Adapter for a *browsable hierarchical* file, or None if abspath is not
    one (single-array .b2z, corrupt/non-container file, wrong suffix)."""
    suffix = abspath.suffix
    if suffix == ".b2z":
        try:
            tree = blosc2.open(abspath)
        except Exception:
            return None
        return _TreeStoreAdapter(tree) if isinstance(tree, blosc2.TreeStore) else None
    if suffix in {".h5", ".hdf5"}:
        try:
            import h5py

            return _HDF5Adapter(h5py.File(abspath, "r"))
        except Exception:
            return None
    return None
```

Adapter interface (all keys are full `/g/a` strings, matching
`treestore_leaves`):

```python
class _ContainerAdapter:
    def leaves(self, prefix="/") -> list[str]: ...  # deep leaf keys
    def size(self, prefix="/") -> int | None: ...  # cheap on-disk bytes
    def get(self, key): ...  # -> leaf-obj | group-marker
    def is_group(self, node) -> bool: ...
```

- `_TreeStoreAdapter`: wraps the existing `treestore_leaves`/`treestore_size`;
  `get(key)` = `tree[key]` (a `TreeStore` for a group, `NDArray`/`CTable` for a
  leaf); `is_group` = `isinstance(node, blosc2.TreeStore)`.
- `_HDF5Adapter`: `leaves` via `h5file.visititems` collecting datasets;
  `size` via `dset.id.get_storage_size()`; `get(key)` returns the `h5py.Group`
  for a group or a **file-less `HDF5Proxy`** for a dataset;
  `is_group` = `isinstance(node, (h5py.Group, h5py.File))`.
- **`get(key)` returns `None` (→ 404) instead of raising** for URL-supplied
  keys that go wrong: a bogus key (`h5file[key]` raises `KeyError`) and an
  incompatible dataset reached by a hand-typed URL (compound/vlen/scalar —
  enumeration skips them, but `open_leaf` → `b2args_from_h5dset` raises,
  hdf5.py:60). Give `_TreeStoreAdapter.get` the same `KeyError`→`None`
  treatment — the current TreeStore path (`tree[inner_key]`, server.py:417)
  looks like it 500s on a bogus key today; fix it in the same move.
- **Handle ownership**: the adapter owns the `h5py.File`; the `HDF5Proxy`
  returned by `get()` borrows it (`open_leaf` never sets `self.h5file`, so
  `HDF5Proxy.__del__` closes nothing). `close()` the adapter only after the
  response bytes are materialized — fine for `get_info` (metadata is read
  eagerly) and `fetch_data` (`to_cframe` returns bytes), but make the ordering
  explicit with a `with` around the whole handler section.

Why this stays bounded: `fetch_data` already type-dispatches on
`hdf5.HDF5Proxy` (server.py:578, 642), so once `get()` hands back a proxy for an
`.h5` leaf, the whole slice/stream path works unchanged.

## Step 1 — File-less `HDF5Proxy` (~15 lines, `caterva2/hdf5.py`)

`HDF5Proxy.__init__` today has two modes: reopen-from-proxy-file (`b2arr is not
None`) and create-proxy-file-on-disk (`urlpath=...`, writes to disk). Add a
third that builds an **in-memory** b2arr with no urlpath:

```python
@classmethod
def open_leaf(cls, h5file, dsetname):
    self = cls.__new__(cls)
    self.fname = h5file.filename
    self.dsetname = dsetname
    self.dset = h5file[dsetname] if dsetname else h5file
    b2args = b2args_from_h5dset(self.dset)  # existing helper
    self.b2arr = blosc2.empty(self.dset.shape or (), dtype=self.dset.dtype, **b2args)
    return self
```

Verified against the existing code: every read path uses only `self.dset` and
`self.b2arr.cparams` — `__getitem__` (hdf5.py:457), `slice` (:481), `to_cframe`
(:527), `cbytes` = `self.dset.id.get_storage_size()` (:418) — so no urlpath is
needed. Guard incompatible datasets with the existing
`h5dset_is_compatible(self.dset)` (compound/vlen/scalar) — skip in enumeration
rather than build a broken proxy.

## Step 2 — Enumerate + size helpers (~12 lines, `caterva2/hdf5.py`)

Analogs of `treestore_leaves`/`treestore_size`:

```python
def hdf5_leaves(h5file, prefix="/"):
    out = []
    grp = h5file[prefix.strip("/")] if prefix.strip("/") else h5file
    grp.visititems(
        lambda name, obj: (
            out.append("/" + name)
            if isinstance(obj, h5py.Dataset) and h5dset_is_compatible(obj)
            else None
        )
    )
    base = prefix.rstrip("/")
    return [f"{base}{k}" for k in out]  # full keys, mirroring treestore_leaves


def hdf5_size(h5file, prefix="/"):
    return sum(
        h5file[k.strip("/")].id.get_storage_size() for k in hdf5_leaves(h5file, prefix)
    )
```

(Feasibility already spot-checked per `b2z-h5-tree.md`: `visititems`,
`get_storage_size`, and urlpath-less `blosc2.empty` all work.)

## Step 3 — `read_metadata` (~8 lines, `srv_utils.py`)

Two edits:
- Plain `.h5` (no inner key) currently returns `models.File` (srv_utils.py:142-
  151). Flip to `models.Directory` (the root group) with `nfiles`/`size` from
  the adapter, mirroring the `TreeStore` branch at :168.
- Add a **dedicated `HDF5Proxy` branch** to the leaf-metadata section (not an
  extension of the NDArray branch: an `HDF5Proxy` isn't an `NDArray`, and the
  existing proxy handling at srv_utils.py:178-181 is keyed on
  `vlmeta["_ftype"]`, which a file-less proxy doesn't have). The branch reads
  shape/dtype/cparams from `proxy.b2arr` and must also set `schunk.cbytes`/
  `schunk.cratio` from the proxy (mirroring :180-181), otherwise cratio shows 0.

## Step 4 — Server descent call sites (~25 lines, `server.py`)

Replace the hardcoded `.b2z`/`blosc2.open`/`TreeStore` logic with the adapter.
`split_container_path` needs **no change** — it already keys off
`BLOSC2_CONTAINER_SUFFIXES`, so adding `.h5`/`.hdf5` (Step in the constant
above) makes it split `@public/foo.h5/g/dset` for free.

- `get_list` (server.py:371-385): if `container := open_container(directory)`,
  return `sorted(k[len(strip):] for k in container.leaves(prefix))`. Drop the
  `directory.suffix == ".b2z"` gate.
- `get_info` (server.py:412-426): on `inner_key`, `container =
  open_container(abspath); node = container.get(inner_key)`; if
  `container.is_group(node)` → `models.Directory(size=container.size(inner_key),
  nfiles=len(container.leaves(inner_key)), mtime=...)`; else
  `read_metadata(node, mtime=abspath.stat().st_mtime)`.
- `fetch_data` (server.py:552-565): admit `.h5`/`.hdf5` at the suffix
  allow-list (:555) **only when `inner_key is not None`** — a plain `.h5` with
  no inner key would otherwise fall through to `open_b2(abspath, path)` (:573)
  → `blosc2.open` on an HDF5 file → 500. A plain `.h5` is a group: keep the 400
  (or 404, like the TreeStore-root case at :564). On `inner_key`, `node =
  open_container(abspath).get(inner_key)`; 404 if `None` or a group, else feed
  `node` (a native leaf or an `HDF5Proxy`) into the existing slice/stream
  machinery (which already knows `HDF5Proxy`).

## Step 5 — Web / virtual roots (~20 lines, `server.py`)

Generalize the two hardcoded `.b2z` spots in `htmx_path_list` (post-virtual-
roots code at server.py:1728 and 1740) to the adapter, plus the two handlers
that open leaves directly:

- Walk loop "is this a mountable container?" (:1728-1736): replace
  `if relpath.suffix == ".b2z": tree = blosc2.open(...); if isinstance(tree,
  TreeStore): add_dataset(..., mountable=True); continue` with a suffix check
  against `srv_utils.BLOSC2_CONTAINER_SUFFIXES` plus a **cheap mountability
  probe**: for `.h5`/`.hdf5` use `h5py.is_hdf5(abspath)` (signature check, no
  file open — this loop runs per `.h5` per search keystroke, and
  `open_container` would open-and-discard an `h5py.File` each time); for `.b2z`
  keep `open_container(abspath) is not None` (must open to tell TreeStore from
  single CTable). Wrap both as e.g. `srv_utils.is_container_file(abspath)`.
- Virtual-root leaf expansion (:1740-1764): replace `tree = blosc2.open(...);
  isinstance TreeStore; treestore_leaves(tree)` with
  `container = srv_utils.open_container(abspath); if container is None: continue;
  size = abspath.stat().st_size; for key in container.leaves(): add_dataset(
  f"{root}{key}", abspath, size=size)`.

`htmx_root_list` needs **no change** — it already accepts arbitrary container
paths in `mounted` and only validates the `@personal/@shared/@public` prefix.

`htmx_path_info` and `htmx_path_view` do **not** inherit `.h5` support from
Step 4: both open leaves directly with `blosc2.open(abspath)[inner_key]`
(server.py:1855 and :1989), which raises on an `.h5` — clicking a mounted
`.h5` leaf would error even with Steps 1-4 done. Route both through the
adapter (`open_container(abspath).get(inner_key)`, `None` → 404/htmx_error);
`htmx_path_info` then feeds the node to `read_metadata` as today.

Net web effect: an `.h5` shows a single row with the plug icon, mounts as a
virtual root, and lists its datasets as leaf rows — identical UX to `.b2z`.

## Step 6 — Client: zero change

`Root.__getitem__` already dispatches on the server-reported `kind`
(`group`→`Group`, `ctable`→`Table`, shape→`Array`), so `.h5` groups/leaves map
correctly with no client edit.

## Decisions / risks

- **Keep `unfold` intact.** Virtual descent and the legacy proxy-file `unfold`
  target `.h5` differently; retiring `unfold` (drop the command +
  `create_hdf5_proxies` + the proxy-file reconstruct branch of `HDF5Proxy`) is a
  clean follow-up once this proves out.
- **`read_metadata` compat flip.** Plain `.h5` changes `File`→`Directory`; a few
  `test_hdf5_proxy` assertions may expect `File` — check/adjust (grep
  `read_metadata` and `kind == "file"` in tests).
- **`split_container_path` widening.** Adding `.h5` means any path with a
  non-final `.h5` segment is treated as descent. Legacy unfold proxies live in a
  `dirname/` (no `.h5` in a non-final segment) so there is no collision — but
  add a test asserting a plain `@public/foo.h5` (no inner key) still resolves to
  the container, not a descent.
- **Untrusted mount paths.** The web loop already swallows open failures
  (`open_container` returns None on any exception); keep that — a mounted
  path is client `localStorage` and must never 500 the listing.
- **h5py import.** Server already imports `hdf5` (which imports `h5py`); keep the
  `import h5py` local to the adapter so a no-HDF5 install degrades to "`.h5`
  isn't a container" rather than an ImportError at module load.
- **File handle lifetime.** `open_container` opens an `h5py.File` per call; the
  adapter owns it (the proxy borrows it — see the adapter section), so give the
  adapter a `close()`/context-manager and close only after response bytes are
  materialized. The hot path (path-list per keystroke) avoids opens entirely
  via `h5py.is_hdf5` (Step 5).

## Suggested increments

1. **Spike:** Step 1 (file-less proxy) + Step 2 (enumeration) with a unit test
   over a scratch `.h5` — prove `open_leaf(...).slice(...)` round-trips and
   `hdf5_leaves`/`hdf5_size` match `visititems`/`get_storage_size`.
2. **Adapter + API:** Steps 3–4 behind the adapter; extend
   `caterva2/tests/test_treestore.py` (or a new `test_hdf5_tree.py`) with
   list/info/fetch over an `.h5` dropped into `@public`, mirroring the existing
   TreeStore tests.
3. **Web:** Step 5; extend the web tests (single mountable row, virtual-root
   leaf expansion, leaf click via `htmx_path_info`/`htmx_path_view`,
   bogus-`.h5` and bogus-inner-key no-500) that already exist for `.b2z`.

## Verification (end-to-end)

Reuse the `.b2z` recipe from `plans/b2z-virtual-roots.md`, substituting an `.h5`
file:

1. Drop a multi-dataset `.h5` into `@public`; run the server (`blosc2` env,
   free port), log in.
2. `cat2-client ... list @public/foo.h5` → dataset keys; `... info
   @public/foo.h5/g/dset` → shape/dtype; `... info @public/foo.h5` → group
   summary (nfiles/size).
3. Web: `foo.h5` shows one row + plug icon (not expanded). Mount it → virtual
   root appears; check it → leaf rows; click a leaf → Meta + data render.
4. `pytest caterva2/tests/test_hdf5_proxy.py caterva2/tests/test_treestore.py`
   green; adjust any `File`→`Directory` assertions.
