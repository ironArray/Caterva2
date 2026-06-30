"""Tests for CTable .b2z metadata and fetch deserialization in Caterva2."""
# ruff: noqa: RUF009  # blosc2.field() is the standard CTable dataclass default API

import pathlib
import tempfile
from dataclasses import dataclass

import blosc2
import blosc2.ctable as ct

from caterva2.services import srv_utils


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
