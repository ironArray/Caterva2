# Caterva2 CTable support plan

## Goal

Add first-class support for Blosc2 heterogeneous tables (`blosc2.CTable`), mainly compact single-file `.b2z` files, alongside the existing `NDArray`/`.b2nd` support.

The first version should make tables discoverable, downloadable, inspectable, and previewable through the REST API, Python client, CLI, and web UI.

## Non-goals for the first pass

Keep the first implementation boring and small. Do **not** build a dataframe service yet.

Skip initially:

- `.b2d` directory-backed tables
- table append/edit/delete over Caterva2
- joins/groupby/describe/cov server APIs
- Arrow/Parquet/CSV transport APIs
- lazy expressions involving `CTable`
- treating `CTable` as a `blosc2.Operand`
- full query language/UI
- server-side pagination cache beyond simple `start`/`stop`

Add these only after a real use case needs them.

## Current assumptions in Caterva2

Caterva2 currently mostly assumes Blosc2 datasets are homogeneous arrays:

- `.b2nd`: `blosc2.NDArray`
- `.b2frame`: `blosc2.NDArray` or `SChunk`-style frame
- `.b2`: compressed regular file / `SChunk`

Important places with array assumptions:

- `caterva2/services/srv_utils.py`
  - `read_metadata()` only accepts `.b2frame`, `.b2nd`, `.b2`
  - metadata model assumes `shape`, `chunks`, `blocks`, `dtype`, `schunk`

- `caterva2/services/server.py`
  - suffix checks in `get_abspath()`, `/api/fetch`, upload, htmx upload/download paths
  - `open_b2()` assumes objects have either `schunk` or `vlmeta`, then later sets `cparams/dparams`
  - `/api/fetch` returns Blosc2 binary frames and decodes naturally as arrays/schunks
  - web preview path assumes `arr.shape`

- `caterva2/models.py`
  - no table metadata model

- `caterva2/client.py`
  - `Root.__getitem__()` returns `Dataset` only for `.b2nd`/`.b2frame`
  - `Dataset` assumes `shape`, `dtype`, `chunks`, `blocks`
  - `Client._fetch_data()` assumes binary cframe response

- `caterva2/clients/cli.py`
  - `show` currently calls `client.fetch()`
  - `info` formatting assumes array/schunk metadata

## Design principles

1. **CTable is a sibling data kind, not an NDArray variant.**
   - Use `nrows`, `ncols`, `columns`, `schema`.
   - Do not invent fake `shape`/`dtype` for API metadata.

2. **Do not overload `/api/fetch` with JSON.**
   - `/api/fetch` currently means Blosc2 binary frame data.
   - Keep it for arrays/schunks.
   - Add a separate table rows endpoint for JSON previews.

3. **Start with compact `.b2z`.**
   - It is a single file and fits Caterva2's current file model.
   - `.b2d` can come later when directory-backed datasets are needed.

4. **Centralize suffix logic.**
   - Avoid adding `.b2z` manually in many places without a shared constant.

5. **Small preview first.**
   - JSON rows for `start`/`stop` and optional columns are enough for UI, CLI, and Python client.
   - Large exports can use `/api/download` first.

## Proposed suffix constants

Add shared constants, preferably in `caterva2/services/srv_utils.py` or a small shared module if client/server both need them.

Server-side minimum:

```python
BLOSC2_ARRAY_SUFFIXES = {".b2nd", ".b2frame"}
BLOSC2_TABLE_SUFFIXES = {".b2z"}
BLOSC2_FRAME_SUFFIXES = {".b2"}
BLOSC2_NATIVE_SUFFIXES = (
    BLOSC2_ARRAY_SUFFIXES | BLOSC2_TABLE_SUFFIXES | BLOSC2_FRAME_SUFFIXES
)
HDF5_SUFFIXES = {".h5", ".hdf5"}
```

Use these in:

- `read_metadata()` suffix assertions
- `get_abspath()` native-file detection
- upload/load-from-url native-file detection
- htmx upload compression decisions
- download handling
- path-list handling for compressed regular files

Client-side can use a tiny local constant:

```python
DATASET_SUFFIXES = (".b2nd", ".b2frame")
TABLE_SUFFIXES = (".b2z",)
```

No need to over-share constants across package boundaries unless already easy.

## Metadata model

Add a new Pydantic model in `caterva2/models.py`:

```python
class CTableMetadata(pydantic.BaseModel):
    kind: str = "ctable"
    nrows: int
    ncols: int
    chunks: tuple
    blocks: tuple
    schema: dict
    columns: list[str]
    nbytes: int
    cbytes: int
    cratio: float
    vlmeta: dict = {}
    mtime: datetime.datetime | None
```

Notes:

- `schema` should come from `table.schema_dict()`.
- `columns` should be derived from the schema for convenience.
- `vlmeta` should use `table.vlmeta`.
- `kind` lets clients distinguish tables without relying on missing `shape`.

Possible future fields:

- `indexes`
- `computed_columns`
- per-column compression stats
- per-column logical/physical dtype info

Skip them initially unless they are already trivial and stable in Blosc2.

## `read_metadata()` changes

In `caterva2/services/srv_utils.py`:

1. Allow `.b2z` in the suffix check.
2. After `obj = blosc2.open(path)`, detect `blosc2.CTable`.
3. Return `models.CTableMetadata`.

Sketch:

```python
if path.suffix not in BLOSC2_NATIVE_SUFFIXES:
    ...

obj = blosc2.open(path)
...
if isinstance(obj, blosc2.CTable):
    schema = obj.schema_dict()
    return models.CTableMetadata(
        nrows=obj.nrows,
        ncols=obj.ncols,
        chunks=obj.chunks,
        blocks=obj.blocks,
        schema=schema,
        columns=[col["name"] for col in schema.get("columns", [])],
        nbytes=obj.nbytes,
        cbytes=obj.cbytes,
        cratio=obj.cratio,
        vlmeta=obj.vlmeta,
        mtime=mtime,
    )
```

Keep the existing `NDArray`, `SChunk`, `LazyArray` branches unchanged.

## `open_b2()` changes

In `caterva2/services/server.py`, `open_b2()` currently opens any Blosc2 object and then eventually tunes `container.cparams.nthreads` and `container.dparams.nthreads`.

`CTable` does not expose table-level `cparams/dparams` like `NDArray`.

Add an early branch after `blosc2.open()` and special-file detection setup:

```python
container = blosc2.open(abspath)

if isinstance(container, blosc2.CTable):
    return container
```

This should happen before trying to set `container.cparams` or `container.dparams`.

If `vlmeta` access is needed before this branch, keep it safe:

```python
vlmeta = (
    container.schunk.vlmeta
    if hasattr(container, "schunk")
    else getattr(container, "vlmeta", {})
)
```

## Path resolution and upload/download

### `get_abspath()`

Currently regular files are auto-compressed to `.b2` unless the suffix is native.

Change native suffix check from:

```
if filepath.suffix not in {".b2frame", ".b2nd", ".h5"}:
```

to include `.b2z` and `.hdf5` via constants:

```
if filepath.suffix not in BLOSC2_ARRAY_SUFFIXES | BLOSC2_TABLE_SUFFIXES | HDF5_SUFFIXES:
```

This prevents uploaded/existing `.b2z` files from being wrapped into `.b2`.

### Upload APIs

Include `.b2z` wherever native Blosc2 files should be stored as-is:

- `/api/upload/{path:path}`
- `/api/load_from_url/{path:path}`
- `/htmx/upload/{name}`

Existing logic:

```python
if abspath.suffix not in {".b2", ".b2frame", ".b2nd"}:
    schunk = blosc2.SChunk(data=data)
```

Should become:

```python
if abspath.suffix not in BLOSC2_NATIVE_SUFFIXES:
    schunk = blosc2.SChunk(data=data)
```

And final write-as-cframe checks should include `.b2z`, `.h5`, `.hdf5` as native.

### Download API

`/api/download/{path:path}` should work for `.b2z` without special conversion.

`get_file_content()` should return stored bytes for `.b2z`. Do not try `to_cframe()`.

If current code opens `.b2nd`/`.b2frame` specially for HDF5Proxy, leave `.b2z` on the normal file path.

## Table rows REST API

Add a new endpoint:

```text
GET /api/table/{path:path}?start=0&stop=50&columns=a,b
```

Response:

```json
{
  "kind": "ctable",
  "start": 0,
  "stop": 50,
  "nrows": 1000000,
  "columns": ["a", "b"],
  "rows": [
    {"a": 1, "b": "foo"},
    {"a": 2, "b": "bar"}
  ]
}
```

Parameters:

- `start: int = 0`
- `stop: int = 50`
- `columns: str | None = None` comma-separated

Validation:

- reject non-CTable with 400
- clamp `start >= 0`
- clamp `stop <= table.nrows`
- enforce `stop >= start`
- optionally cap page size, e.g. `max_rows = 1000`

Sketch:

```python
@app.get("/api/table/{path:path}")
async def get_table_rows(
    path: pathlib.Path,
    start: int = 0,
    stop: int = 50,
    columns: str | None = None,
    user: db.User = Depends(optional_user),
):
    table = open_b2(get_abspath(path, user), path)
    if not isinstance(table, blosc2.CTable):
        srv_utils.raise_bad_request("Not a CTable")

    max_rows = 1000
    start = max(start, 0)
    stop = min(max(stop, start), table.nrows, start + max_rows)

    schema = table.schema_dict()
    all_columns = [col["name"] for col in schema.get("columns", [])]
    selected = [c for c in columns.split(",") if c] if columns else all_columns
    unknown = sorted(set(selected) - set(all_columns))
    if unknown:
        srv_utils.raise_bad_request(f"Unknown columns: {unknown}")

    view = table[selected] if selected != all_columns else table
    rows = [_jsonable_row(row) for row in view.slice(start, stop)]

    return {
        "kind": "ctable",
        "start": start,
        "stop": stop,
        "nrows": table.nrows,
        "columns": selected,
        "rows": rows,
    }
```

Add small helpers:

```python
def _jsonable(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _jsonable_row(row):
    if hasattr(row, "_asdict"):
        row = row._asdict()
    return {k: _jsonable(v) for k, v in dict(row).items()}
```

If `dict(row)` is not reliable for CTable row objects, use `_asdict()` first.

## `/api/fetch` behavior

Do not support `.b2z` in `/api/fetch` initially.

Change the error message to be accurate:

```python
if abspath.suffix not in BLOSC2_ARRAY_SUFFIXES:
    srv_utils.raise_bad_request(
        "The fetch API only supports array datasets (.b2nd and .b2frame); "
        "use /api/table for CTable row previews or /api/download for files"
    )
```

Reason:

- `/api/fetch` returns binary Blosc2 frames.
- CTable does not currently fit the `ndarray_from_cframe`/`schunk_from_cframe` client path.
- JSON rows belong in a separate endpoint.

## Python client API

Add `Table` class in `caterva2/client.py`:

```python
class Table(File):
    def __repr__(self):
        return f"<Table: {self.path}>"

    @property
    def nrows(self):
        return self.meta["nrows"]

    @property
    def ncols(self):
        return self.meta["ncols"]

    @property
    def columns(self):
        return self.meta["columns"]

    @property
    def schema(self):
        return self.meta["schema"]

    def rows(self, start=0, stop=50, columns=None):
        return self.client.get_table_rows(
            self.path, start=start, stop=stop, columns=columns
        )

    def head(self, n=10, columns=None):
        return self.rows(0, n, columns=columns)
```

Change `Root.__getitem__()`:

```python
if path.endswith((".b2nd", ".b2frame")):
    return Dataset(self, path)
if path.endswith(".b2z"):
    return Table(self, path)
return File(self, path)
```

Change `Client.get()` type check:

```python
if isinstance(path, (File, Dataset, Table, Root)):
    return path
```

Add `Client.get_table_rows()`:

```python
def get_table_rows(self, path, start=0, stop=50, columns=None):
    if isinstance(path, Table):
        path = path.path
    _, path = _format_paths(self.urlbase, path)
    params = {"start": start, "stop": stop}
    if columns:
        params["columns"] = (
            ",".join(columns) if not isinstance(columns, str) else columns
        )
    return self._get(
        f"{self.urlbase}/api/table/{path}",
        params=params,
        auth_cookie=self.cookie,
        timeout=self.timeout,
    )
```

Return the full response dict first. If callers want rows only:

```python
table.head()["rows"]
```

A later ergonomic wrapper can return just rows, but the full response is more useful and avoids adding options.

## CLI changes

### `info`

If metadata contains `kind == "ctable"`, print table-specific fields:

```text
kind   : ctable
nrows  : 1000000
ncols  : 12
chunks : (1048576,)
blocks : (32768,)
nbytes : 123 MB
cbytes : 12 MB
ratio  : 10.2x
columns: a, b, c, ...
mtime  : ...
```

For `--json`, no special work: dump metadata as returned.

### `show`

For `.b2z`, do not call `client.fetch()`.

Minimal behavior:

```sh
cat2-client show path/table.b2z
```

prints first 50 rows.

Optional first-pass slice support:

```sh
cat2-client show path/table.b2z[0:20]
```

maps to `start=0`, `stop=20`.

Optional columns could come later via `--columns a,b`. Skip unless asked.

Implementation:

- detect `.b2z` path
- parse single row slice only
- call `client.get_table_rows()`
- print rows as a simple table or JSON

Lazy first text output can use standard formatting:

```python
rows = data["rows"]
print(json.dumps(rows, indent=2))
```

Pretty tables can come later.

## Web UI changes

### `htmx_path_info()`

Currently array display tab is added when `hasattr(meta, "shape")`.

Add table display tab when `kind == "ctable"` or `hasattr(meta, "nrows")`:

```python
if getattr(meta, "kind", None) == "ctable":
    context["data_url"] = make_url(request, "htmx_path_view", path=path)
    context["shape"] = (meta.nrows,)
    tabs.append(
        {
            "name": "data",
            "label": "Display",
            "include": "includes/info_data.html",
        }
    )
```

### `htmx_path_view()`

Add an early CTable branch after `arr = open_b2(...)`:

```
if isinstance(arr, blosc2.CTable):
    schema = arr.schema_dict()
    cols = [col["name"] for col in schema.get("columns", [])]
    fields = fields or cols[:5]
    index = (0,) if index is None else tuple(index)
    sizes = sizes or [10]
    start = index[0]
    stop = min(start + sizes[0], arr.nrows)
    view = arr[fields] if fields else arr
    rows = [fields] + [[row._asdict()[f] for f in fields] for row in view.slice(start, stop)]
    ... render info_view.html ...
```

Reusing `info_view.html` keeps the diff small.

Set context keys similarly to array preview:

```python
context = {
    "view_url": make_url(request, "htmx_path_view", path=path),
    "inputs": [
        {
            "start": start,
            "start_max": max(arr.nrows - size, 0),
            "size": size,
            "size_max": arr.nrows,
            "with_size": True,
        }
    ],
    "rows": rows,
    "cols": cols,
    "fields": fields,
    "filter": "",
    "sortby": "",
    "shape": (arr.nrows,),
    "tags": list(range(start, stop)),
}
```

Filtering/sorting can come later. CTable has `where()`, `sort_by()`, `sorted_slice()`, but adding it now risks coupling Caterva2 to a new query surface.

## Tests

Add one small CTable fixture.

Example creation helper:

```python
from dataclasses import dataclass
import blosc2


@dataclass
class Row:
    id: int = blosc2.field(blosc2.int32())
    name: str = blosc2.field(blosc2.string(max_length=20))


def make_ctable(path):
    table = blosc2.CTable(Row, urlpath=str(path), mode="w", compact=True)
    table.append((1, "alice"))
    table.append((2, "bob"))
    table.append((3, "carol"))
    table.close()
```

Tests:

1. `read_metadata()` on `.b2z`
   - `kind == "ctable"`
   - `nrows == 3`
   - `columns == ["id", "name"]`

2. `/api/info/...table.b2z`
   - same assertions through HTTP

3. `/api/download/...table.b2z`
   - write response to temp file
   - `blosc2.open(downloaded_path)` returns `blosc2.CTable`
   - `len(table) == 3`

4. `/api/table/...table.b2z?start=1&stop=3`
   - response rows are bob/carol
   - columns are present

5. Python client
   - `root["table.b2z"]` is `Table`
   - `table.nrows == 3`
   - `table.head(2)["rows"]` has two rows

6. CLI smoke test
   - `cat2-client info table.b2z --json` contains `kind: ctable`
   - `cat2-client show table.b2z --json` returns first rows

Keep tests small. No fixtures for huge data, no performance suite.

## Edge cases

### NumPy scalar JSON encoding

CTable rows can contain NumPy scalar types. Convert with `.item()`.

### NumPy array cells

CTable can have nested/NDArray-like cells. For JSON preview, convert arrays to lists.

### Bytes cells

Decode as UTF-8 with replacement for preview. For exact bytes transport, use download/export later.

### Large pages

Cap JSON rows, e.g. 1000 rows per request. This prevents accidental huge responses from the web UI or CLI.

### Unknown columns

Reject unknown requested columns with 400. Do not silently ignore.

### `.b2d`

Directory-backed CTable should be rejected/not recognized initially. If uploaded as archive, it is just a regular file/archive. Native directory-backed support needs separate path-list and download semantics.

## Future extensions

Only add these once MVP is working and there is demand:

### Table filtering

Use CTable native APIs:

```python
table.where("col > 10", columns=["a", "b"])
```

Possible endpoint:

```text
GET /api/table/{path}?where=col%20%3E%2010&columns=a,b&start=0&stop=50
```

Need careful expression validation/error handling.

### Sorting

Use:

```python
table.sorted_slice(...)
table.sort_by(...)
```

Possible endpoint params:

```text
sort=col
ascending=true
```

### Arrow transport

For larger table reads, add:

```text
GET /api/table/{path}?format=arrow
```

Backed by `table.to_arrow()` or `iter_arrow_batches()`.

Only if PyArrow is already an accepted dependency/extra. Do not add as core dependency just for first support.

### CSV export

Possible:

```text
GET /api/table/{path}?format=csv
```

Backed by `table.to_csv()`.

### Parquet export

Possible:

```text
GET /api/table/{path}?format=parquet
```

Backed by `table.to_parquet()`.

Needs temp files and likely PyArrow. Not MVP.

### `.b2d` support

Directory-backed tables need decisions:

- how to list them as one logical dataset
- how to upload/download them
- whether to tar/zip for download
- how to protect internal files from direct mutation

Do later.

## Suggested implementation order

1. Add suffix constants and `.b2z` native handling.
2. Add `CTableMetadata` and `read_metadata()` support.
3. Make `open_b2()` return `CTable` safely.
4. Ensure `/api/download` preserves `.b2z` bytes.
5. Add `/api/table/{path:path}` JSON row window endpoint.
6. Add Python client `Table` and `Client.get_table_rows()`.
7. Add web preview branch reusing existing templates.
8. Add CLI `info`/`show` support.
9. Add the small CTable tests.

## Open questions

- Should Python `Table.rows()` return the full response dict or just `rows`?
  - Proposed first pass: full dict.

- Should web table preview support filters immediately?
  - Proposed first pass: no.

- Should `.b2z` be included in `/api/fetch` as a full table binary download?
  - Proposed first pass: no; use `/api/download`.

- Should Caterva2 expose Arrow if Blosc2 can export Arrow?
  - Proposed first pass: no; avoid dependency/API expansion.

- Should table metadata include per-column compression stats?
  - Proposed first pass: no, unless Blosc2 exposes a stable cheap dict.

## Definition of done for MVP

A user can:

```python
import caterva2 as cat2

client = cat2.Client("https://server")
root = client.get("@public")
table = root["example.b2z"]

print(table.nrows)
print(table.columns)
print(table.head(5))
table.download()
```

And in CLI:

```sh
cat2-client info @public/example.b2z
cat2-client show @public/example.b2z
```

And in the web UI:

- click a `.b2z`
- see metadata
- see a small row/column preview

That is enough to say Caterva2 serves Blosc2 tables.
