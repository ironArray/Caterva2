# Caterva2 CTable (`.b2z`) — implementation tasks

Task list distilled from `ctable-support.md`. **Rationale, options, and design
decisions live in that document** — this file is only *what to do*, ranked by
importance. Each task lists target files, concrete changes, and a "Done when"
acceptance check.

**Status: all tasks below are done.** No open work remains from this plan.

Priorities: **P0** = blocking bug, ship-stopper. **P1** = core MVP correctness /
API. **P2** = user-facing preview surface. **P3** = polish / deferred.

---

## Baseline already committed (do not redo)

- blosc2: `CTable.to_cframe()` + `blosc2.ctable_from_cframe()` (exported);
  lazy-view `to_cframe()` works.
- `srv_utils.py`: suffix constants incl. `BLOSC2_TABLE_SUFFIXES = {".b2z"}`,
  `BLOSC2_NATIVE_SUFFIXES`.
- `models.py`: `CTableMetadata` (field name is `schema_dict`).
- `srv_utils.read_metadata()`: maps `CTable` → `CTableMetadata`.
- `server.open_b2()`: returns `CTable` early (before `cparams`/`dparams`).
- `server.get_abspath()`: `.b2z` treated as native (line ~459).
- `server` `/api/fetch`: accepts `.b2z`, serializes **slices** via `to_cframe()`.
- `client.py`: `Table` class exists; `Root.__getitem__` returns `Table` for
  `.b2z`; `_fetch_data` tries `ctable_from_cframe` first.

---

## P0 — Blocking

### T1. Fix whole-table `/api/fetch` (returns raw zip, client can't decode) — DONE

- **File:** `caterva2/services/server.py`, `/api/fetch` handler (~line 575).
- **Bug:** the whole-dataset short-circuit returned `FileResponse(abspath)`; a
  whole `.b2z` is a zip (`PK\x03\x04`), not a cframe, so the client's cframe
  decoder raised `RuntimeError: Could not get the schunk from the cframe`.
  `table[:]` (and any slice with `stop >= nrows`) failed.
- **Fix:** added `blosc2.CTable` to the whole-dataset short-circuit exclusion,
  so whole tables also serialize via `to_cframe()` like slices already did.
- **Regression test:** `test_fetch_whole_and_slice` in `test_ctable.py`
  (verified it fails without the fix, passes with it).

---

## P1 — Core MVP

### T2. `_fetch_data`: dispatch decode on known kind (not trial/except) — DONE

- **File:** `caterva2/client.py`, `Client._fetch_data`/`get_slice`.
- `get_slice` now computes `kind` (`"ctable"` vs. not) from the `Table`
  instance or path suffix *before* the request, and `_fetch_data` dispatches
  `ctable_from_cframe` only when `kind == "ctable"`; otherwise the existing
  `ndarray_from_cframe` → `schunk_from_cframe` fallback. No more
  try/except-driven format sniffing.

### T3. Client class hierarchy: `File → Dataset → {Array, Table}` — DONE

- **File:** `caterva2/client.py`, `caterva2/__init__.py`.
- `Dataset(File)` is now a lean shared base (no `blosc2.Operand`).
  `Array(Dataset, blosc2.Operand)` holds `dtype`/`shape`/`ndim`/`chunks`/
  `blocks`/`append` and the dtype-aware `__getitem__` (moved out of `File`,
  which keeps a minimal generic `__getitem__`/`slice()` for plain files).
  `Table(Dataset)`. `Root.__getitem__` returns `Array` for `.b2nd`/`.b2frame`,
  `Table` for `.b2z`. Simplified redundant `isinstance` tuples in
  `Client.get`/`get_info`/`get_slice`/`get_chunk`/`remove`. Updated repr
  doctests. `Array`/`Table` exported from `caterva2/__init__.py`.

### T4. `Table` client behavior — DONE

- **File:** `caterva2/client.py`, `Table(Dataset)`.
- Added `nrows`, `ncols`, `columns`, `schema` (reads `meta["schema_dict"]`),
  `rows(start, stop)`, `head(n)`; existing `slice()`/`__getitem__` return a
  `blosc2.CTable`.

### T5. `.b2z` upload/download suffix handling (+ use constants) — DONE

- **File:** `caterva2/services/server.py`.
- Replaced hardcoded suffix sets with `srv_utils.BLOSC2_NATIVE_SUFFIXES`
  (now includes `.b2z`) in `/api/upload`, `/api/load_from_url`,
  `/htmx/upload` (both archive-extraction and single-file branches), and the
  quick-search path-guessing code. `get_file_content`/`/api/download` needed
  no change — `.b2z` already fell through to the raw-bytes path correctly.
  Verified byte-identical upload→download round-trip.

---

## P2 — Preview surface

### T6. Web UI preview (reuse `info_view.html`) — DONE

- **Files:** `caterva2/services/server.py` (`htmx_path_info`,
  `htmx_path_view`); `info_view.html`; `includes/info_metadata.html`.
- `htmx_path_info`: Display tab added when `meta.kind == "ctable"`,
  `shape = (nrows,)`.
- `htmx_path_view`: dedicated `CTable` branch (before `shape = arr.shape`)
  builds `cols`/`rows`/`inputs`/`tags` from `schema_dict()`/`nrows`, using
  keyed `row[name]` access (see T-blosc2 note below) and coercing
  bytes/numpy scalars for HTML.
- Added a `filterable` context flag (`False` for `CTable`, `True` for
  arrays); `info_view.html` guards the Filter box + Sort-by select with
  `{% if filterable %}` (Fields selector stays available for both).
- Fixed a **pre-existing crash**: `includes/info_metadata.html`'s Meta tab
  assumed any non-`schunk` metadata with `nbytes` also had `cparams` (true
  for plain `.b2frame` `SChunk`, false for `CTableMetadata`), so viewing a
  `.b2z` file's metadata tab threw `UndefinedError`. Added a dedicated
  `ctable` branch.

### T7. CLI `info` / `show` for `.b2z` — DONE

- **File:** `caterva2/clients/cli.py`.
- `info`: prints table-shaped fields (`nrows`, `ncols`, `chunks`, `blocks`,
  `columns`, `nbytes`/`cbytes`/`ratio`, `mtime`) when `kind == "ctable"`
  instead of crashing on `cparams.get(...)` against `None`; `--json`
  unchanged.
- `show`: for `.b2z`, parses the optional row-slice syntax
  (`table.b2z[start:stop]`) client-side and prints `table.rows(start, stop)`
  via the `Table` client class — no `client.fetch()`/`/api/table` call.

---

## P3 — Tests & fixtures

### T8. CTable fixture + tests — DONE

- **File:** `caterva2/tests/test_ctable.py` (22 tests).
- All 7 items from the original list are covered: `read_metadata()` unit
  tests, HTTP `/api/info`, `/api/download` round-trip, `/api/fetch` whole +
  slice (T1 regression guard), Python client `Table` class behavior, CLI
  `info`/`show` (incl. row-slice syntax), and web preview
  (`htmx_path_info`/`htmx_path_view` render without error, Filter/Sort-by
  absent, Fields present).
- Also added regression tests for a bug found during manual testing (see
  note below): non-identifier/nested CTable column names in the web preview.

---

## Post-plan fix: nested/non-identifier column names (found during manual QA)

Manually uploading a real-world table (`chicago-taxi-indexed.b2z`, with
struct columns like `trip.sec`, `trip.begin.lon`) crashed `htmx_path_view`
twice in a row:

1. `row._asdict()[f]` used the *renamed* namedtuple keys (blosc2's
   `CTableRow` is built with `namedtuple(..., rename=True)`, so a column
   named `trip.sec` becomes `_1` internally) instead of blosc2's own
   name-safe `row[name]`/`row.as_dict()`. Fixed in caterva2 by switching to
   `row[f]`.
2. `schema_dict()` flattens nested struct columns into dotted leaf paths
   (`trip.sec` for a `trip` struct column with a `sec` leaf) that don't
   exist as row fields at all — the row only exposes the top-level `trip`
   dict. `row["trip.sec"]` raised `KeyError`.

Root-caused (2) as a genuine blosc2 API gap — `col_names`/`schema_dict()`
advertised names that `CTableRow.__getitem__` couldn't resolve — and fixed
it **upstream in `python-blosc2`** (`CTableRow.__getitem__` now walks dotted
paths into nested structs via `split_field_path`, with tests in
`tests/ctable/test_nested_access_storage.py`). Once that landed, the
caterva2-side workaround was removed and `htmx_path_view` went back to a
plain `row[f]` for every field, relying on blosc2's native support.
