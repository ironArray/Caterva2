"""Tests for CTable .b2z metadata and fetch deserialization in Caterva2."""
# ruff: noqa: RUF009  # blosc2.field() is the standard CTable dataclass default API

import io
import json
import pathlib
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass

import blosc2
import blosc2.ctable as ct
import httpx
import pytest

import caterva2 as cat2
from caterva2.clients import cli
from caterva2.services import srv_utils

from .services import TEST_CATERVA2_ROOT, TEST_STATE_DIR


def _make_table(path, n=5):
    @dataclass
    class Row:
        x: int = blosc2.field(blosc2.int32())
        y: str = blosc2.field(blosc2.string(max_length=20))

    t = blosc2.CTable(Row, urlpath=str(path), mode="w", compact=True)
    for i in range(n):
        t.append((i, f"v{i}"))
    t.close()


# ---------------------------------------------------------------------------
# read_metadata
# ---------------------------------------------------------------------------


def test_read_metadata():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "t.b2z"
    _make_table(table_path)

    meta = srv_utils.read_metadata(table_path)
    assert meta.kind == "ctable"
    assert meta.nrows == 5
    assert meta.ncols == 2
    assert meta.columns == ["x", "y"]
    assert meta.cbytes > 0
    assert meta.nbytes > meta.cbytes
    assert meta.cratio > 1.0
    assert isinstance(meta.chunks, tuple)


def test_read_metadata_empty():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "empty.b2z"

    @dataclass
    class Row:
        x: int = blosc2.field(blosc2.int32())

    t = blosc2.CTable(Row, urlpath=str(table_path), mode="w", compact=True)
    t.close()

    meta = srv_utils.read_metadata(table_path)
    assert meta.nrows == 0
    assert meta.ncols == 1
    assert meta.columns == ["x"]


def test_read_metadata_list_column():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "list.b2z"

    @dataclass
    class Row:
        x: int = blosc2.field(blosc2.int32())
        tags: list[int] = blosc2.field(ct.ListSpec(ct.int32()))

    t = blosc2.CTable(Row, urlpath=str(table_path), mode="w", compact=True)
    for r in [(1, [1, 2]), (2, [3])]:
        t.append(r)
    t.close()

    meta = srv_utils.read_metadata(table_path)
    assert meta.nrows == 2
    assert meta.ncols == 2
    assert meta.columns == ["x", "tags"]


def test_read_metadata_vlstring():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "vlstr.b2z"

    @dataclass
    class Row:
        x: int = blosc2.field(blosc2.int32())
        label: str = blosc2.field(ct.VLStringSpec())

    t = blosc2.CTable(Row, urlpath=str(table_path), mode="w", compact=True)
    for r in [(1, "hi"), (2, "yo")]:
        t.append(r)
    t.close()

    meta = srv_utils.read_metadata(table_path)
    assert meta.nrows == 2
    assert meta.columns == ["x", "label"]


def test_read_metadata_vlmeta():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "vlmeta.b2z"

    @dataclass
    class Row:
        x: int = blosc2.field(blosc2.int32())

    t = blosc2.CTable(Row, urlpath=str(table_path), mode="w", compact=True)
    t.append((0,))
    t.vlmeta["author"] = "Alice"
    t.close()

    meta = srv_utils.read_metadata(table_path)
    assert meta.vlmeta == {"author": "Alice"}


def test_read_metadata_nonexistent():
    import pytest

    tmp = pathlib.Path(tempfile.mkdtemp())
    with pytest.raises(FileNotFoundError):
        srv_utils.read_metadata(tmp / "nope.b2z")


# ---------------------------------------------------------------------------
# fetch deserialization (simulating what /api/fetch and Client._fetch_data do)
# ---------------------------------------------------------------------------


def test_cframe_deserialization_slice():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "t.b2z"
    _make_table(table_path)

    container = blosc2.open(table_path)
    view = container.slice(1, 4)
    cf = view.to_cframe()

    back = blosc2.ctable_from_cframe(cf)
    assert isinstance(back, blosc2.CTable)
    assert len(back) == 3
    assert [tuple(r) for r in back] == [(1, "v1"), (2, "v2"), (3, "v3")]


def test_cframe_deserialization_whole():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "t.b2z"
    _make_table(table_path)

    container = blosc2.open(table_path)
    cf = container.to_cframe()
    back = blosc2.ctable_from_cframe(cf)
    assert len(back) == 5
    assert back[0].x == 0
    assert back[4].x == 4
    assert back[2].y == "v2"


def test_cframe_deserialization_single_row():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "t.b2z"
    _make_table(table_path)

    container = blosc2.open(table_path)
    view = container.slice(2, 3)
    cf = view.to_cframe()
    back = blosc2.ctable_from_cframe(cf)
    assert len(back) == 1
    assert tuple(back[0]) == (2, "v2")


def test_cframe_deserialization_empty_slice():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "t.b2z"
    _make_table(table_path)

    container = blosc2.open(table_path)
    view = container.slice(0, 0)
    cf = view.to_cframe()
    back = blosc2.ctable_from_cframe(cf)
    assert len(back) == 0
    assert back.col_names == ["x", "y"]


def test_cframe_deserialization_multi_column():
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "cols.b2z"

    @dataclass
    class Row:
        a: int = blosc2.field(blosc2.int32())
        b: float = blosc2.field(blosc2.float64())
        c: str = blosc2.field(blosc2.string(max_length=10))

    t = blosc2.CTable(Row, urlpath=str(table_path), mode="w", compact=True)
    for r in [(1, 2.5, "hi"), (2, 3.5, "yo")]:
        t.append(r)
    t.close()

    container = blosc2.open(table_path)
    cf = container.to_cframe()
    back = blosc2.ctable_from_cframe(cf)
    assert back.col_names == ["a", "b", "c"]
    assert back[0].b == 2.5
    assert back[1].c == "yo"


def test_cframe_deserialization_large_slice():
    """Ensure a slice of many rows round-trips correctly."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "big.b2z"
    _make_table(table_path, n=200)

    container = blosc2.open(table_path)
    view = container.slice(50, 150)
    cf = view.to_cframe()
    back = blosc2.ctable_from_cframe(cf)
    assert len(back) == 100
    assert back[0].x == 50
    assert back[99].x == 149


# ---------------------------------------------------------------------------
# HTTP-level / Python client tests (require the test services)
# ---------------------------------------------------------------------------


@pytest.fixture
def fill_ctable_public(client):
    """Drop a small .b2z table straight into the @public root's storage."""
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = "test_table.b2z"
    _make_table(dest_dir / fname, n=3)
    return fname, client.get(TEST_CATERVA2_ROOT)


def test_http_info(fill_ctable_public, client):
    fname, root = fill_ctable_public
    path = f"{root.name}/{fname}"
    info = client.get_info(path)
    assert info["kind"] == "ctable"
    assert info["nrows"] == 3
    assert info["columns"] == ["x", "y"]


def test_http_download_roundtrip(fill_ctable_public, tmp_path):
    fname, root = fill_ctable_public
    table = root[fname]
    local_orig = pathlib.Path(TEST_STATE_DIR) / "server/public" / fname
    downloaded = table.download(tmp_path / "downloaded.b2z")
    assert pathlib.Path(downloaded).read_bytes() == local_orig.read_bytes()

    reopened = blosc2.open(downloaded)
    assert isinstance(reopened, blosc2.CTable)
    assert len(reopened) == 3


def test_fetch_whole_and_slice(fill_ctable_public, client):
    """Regression test for T1: whole-table fetch must not return the raw zip."""
    fname, root = fill_ctable_public
    path = f"{root.name}/{fname}"

    whole = client.get_slice(path, as_blosc2=True)
    assert isinstance(whole, blosc2.CTable)
    assert len(whole) == 3
    assert whole[0].x == 0
    assert whole[2].x == 2

    part = client.get_slice(path, slice(1, 3), as_blosc2=True)
    assert isinstance(part, blosc2.CTable)
    assert len(part) == 2
    assert tuple(part[0]) == (1, "v1")
    assert tuple(part[1]) == (2, "v2")


def test_fetch_negative_index(fill_ctable_public, client):
    """Regression (PR #288 review): negative indexing must not clamp to empty.

    `table[-1]` previously resolved to an empty slice because ``row_stop`` was
    derived (``sl0 + 1`` -> 0) before negative normalization was applied.
    """
    fname, root = fill_ctable_public
    path = f"{root.name}/{fname}"  # 3 rows: (0, "v0"), (1, "v1"), (2, "v2")

    # The exact failing case: -1 must return the last row, not an empty table.
    last = client.get_slice(path, -1, as_blosc2=True)
    assert isinstance(last, blosc2.CTable)
    assert len(last) == 1
    assert tuple(last[0]) == (2, "v2")

    # A non-(-1) negative int also resolves to a single row.
    second_last = client.get_slice(path, -2, as_blosc2=True)
    assert len(second_last) == 1
    assert tuple(second_last[0]) == (1, "v1")

    # Negative slice start still works.
    tail = client.get_slice(path, slice(-2, None), as_blosc2=True)
    assert len(tail) == 2
    assert tuple(tail[0]) == (1, "v1")
    assert tuple(tail[1]) == (2, "v2")


def test_client_table_class(fill_ctable_public):
    fname, root = fill_ctable_public
    table = root[fname]

    assert isinstance(table, cat2.Table)
    assert isinstance(table, cat2.Dataset)
    assert not isinstance(table, cat2.Array)

    assert table.nrows == 3
    assert table.ncols == 2
    assert table.columns == ["x", "y"]

    assert table.head(2) == [(0, "v0"), (1, "v1")]

    whole = table[:]
    assert isinstance(whole, blosc2.CTable)
    assert len(whole) == 3

    part = table.slice(slice(1, 3))
    assert isinstance(part, blosc2.CTable)
    assert len(part) == 2


# ---------------------------------------------------------------------------
# CLI: info / show for .b2z
# ---------------------------------------------------------------------------


def _run_cli(argv):
    buf = io.StringIO()
    old_argv = sys.argv
    sys.argv = ["cat2-client", *argv]
    try:
        with redirect_stdout(buf):
            cli.main()
    finally:
        sys.argv = old_argv
    return buf.getvalue()


def test_cli_info_json(fill_ctable_public, services):
    fname, root = fill_ctable_public
    path = f"{root.name}/{fname}"
    out = _run_cli(["--url", services.get_urlbase(), "info", path, "--json"])
    data = json.loads(out.strip().splitlines()[-1])
    assert data["kind"] == "ctable"
    assert data["nrows"] == 3


def test_cli_info_text(fill_ctable_public, services):
    fname, root = fill_ctable_public
    path = f"{root.name}/{fname}"
    out = _run_cli(["--url", services.get_urlbase(), "info", path])
    assert "nrows  : 3" in out
    assert "columns: ['x', 'y']" in out


def test_cli_show(fill_ctable_public, services):
    fname, root = fill_ctable_public
    path = f"{root.name}/{fname}"
    out = _run_cli(["--url", services.get_urlbase(), "show", path])
    assert "(0, 'v0')" in out
    assert "(2, 'v2')" in out


def test_cli_show_slice(fill_ctable_public, services):
    fname, root = fill_ctable_public
    path = f"{root.name}/{fname}"
    out = _run_cli(["--url", services.get_urlbase(), "show", f"{path}[1:3]"])
    lines = [line for line in out.strip().splitlines() if line.startswith("(")]
    assert lines == ["(1, 'v1')", "(2, 'v2')"]


# ---------------------------------------------------------------------------
# Web preview: htmx_path_view/htmx_path_info render .b2z without error
# ---------------------------------------------------------------------------


def test_htmx_path_info_renders(fill_ctable_public, client):
    fname, root = fill_ctable_public
    path = f"{root.name}/{fname}"
    resp = httpx.get(f"{client.urlbase}/htmx/path-info/{path}")
    resp.raise_for_status()
    assert "Display" in resp.text
    assert "Meta" in resp.text
    assert "nrows" in resp.text


def test_htmx_path_view_no_filter_sort(fill_ctable_public, client):
    fname, root = fill_ctable_public
    path = f"{root.name}/{fname}"
    resp = httpx.post(f"{client.urlbase}/htmx/path-view/{path}")
    resp.raise_for_status()
    text = resp.text
    # Fields selector present, Filter/Sort-by absent (filterable=False for CTable)
    assert "Fields" in text
    assert "Sort by" not in text
    assert 'placeholder="Filter"' not in text
    assert "v0" in text
    assert "v2" in text


# ---------------------------------------------------------------------------
# Regression: columns whose name is not a valid Python identifier (namedtuple
# renames them under the hood, e.g. via CTable.from_arrow with a dotted name)
# ---------------------------------------------------------------------------


def test_ctable_row_non_identifier_column_access():
    """row[name] must be used instead of row._asdict()[name]: namedtuple(rename=True)
    replaces non-identifier field names (e.g. "trip.sec") with "_N" internally."""
    row_cls = ct._make_namedtuple_row_type(("id", "trip.sec", "name"))
    row = row_cls(1, 100, "a")
    assert row["trip.sec"] == 100
    assert "trip.sec" not in row._asdict()
    with pytest.raises(KeyError):
        row._asdict()["trip.sec"]


@pytest.fixture
def fill_dotted_ctable_public(client):
    pa = pytest.importorskip("pyarrow")

    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "dotted_src.b2z"
    schema = pa.schema([("id", pa.int32()), ("trip.sec", pa.int32())])
    batch = pa.record_batch([pa.array([1, 2]), pa.array([100, 200])], schema=schema)
    t = blosc2.CTable.from_arrow(schema, [batch], urlpath=str(table_path), mode="w")
    t.close()

    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = "dotted.b2z"
    (dest_dir / fname).write_bytes(table_path.read_bytes())
    return fname, client.get(TEST_CATERVA2_ROOT)


def test_htmx_path_view_dotted_column(fill_dotted_ctable_public, client):
    fname, root = fill_dotted_ctable_public
    path = f"{root.name}/{fname}"
    resp = httpx.post(f"{client.urlbase}/htmx/path-view/{path}")
    resp.raise_for_status()
    assert "trip.sec" in resp.text
    assert "100" in resp.text


@pytest.fixture
def fill_nested_ctable_public(client):
    """A struct column ('trip') whose leaves schema_dict() reports as dotted
    paths ("trip.sec") that don't exist as top-level row fields at all: the
    row only exposes 'trip' itself (a nested dict)."""
    pa = pytest.importorskip("pyarrow")

    tmp = pathlib.Path(tempfile.mkdtemp())
    table_path = tmp / "nested_src.b2z"
    trip_type = pa.struct([("sec", pa.float32()), ("km", pa.float32())])
    schema = pa.schema([("id", pa.int32()), ("trip", trip_type)])
    batch = pa.record_batch(
        [
            pa.array([1, 2]),
            pa.array([{"sec": 10.0, "km": 1.0}, {"sec": 20.0, "km": 2.0}], type=trip_type),
        ],
        schema=schema,
    )
    t = blosc2.CTable.from_arrow(schema, [batch], urlpath=str(table_path), mode="w")
    t.close()

    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = "nested.b2z"
    (dest_dir / fname).write_bytes(table_path.read_bytes())
    return fname, client.get(TEST_CATERVA2_ROOT)


def test_htmx_path_view_nested_struct_column(fill_nested_ctable_public, client):
    """Regression: schema_dict() flattens struct leaves as "trip.sec", but the
    row itself only has a top-level "trip" dict field; row["trip.sec"] raises
    KeyError and must fall back to walking row["trip"]["sec"]."""
    fname, root = fill_nested_ctable_public
    path = f"{root.name}/{fname}"
    resp = httpx.post(f"{client.urlbase}/htmx/path-view/{path}")
    resp.raise_for_status()
    assert "trip.sec" in resp.text
    assert "10.0" in resp.text
    assert "20.0" in resp.text
