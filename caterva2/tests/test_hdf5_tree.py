"""Tests for .h5/.hdf5 virtual descent (list/info/fetch of inner leaves),
mirroring test_treestore.py for the .b2z/TreeStore case."""

import pathlib

import blosc2
import h5py
import httpx
import numpy as np
import pytest

from caterva2.services import srv_utils

from .services import TEST_CATERVA2_ROOT, TEST_STATE_DIR


def _make_h5(path):
    with h5py.File(str(path), "w") as f:
        f.create_dataset("/g/a", data=np.arange(6, dtype="i4").reshape(2, 3))
        f.create_dataset("/g/b", data=np.arange(4, dtype="i4"))
        f.create_dataset("/h/c", data=np.arange(10, dtype="i4"))
        # Structured leaf for sort tests
        dtype = np.dtype([("x", "i4"), ("y", "f8")])
        data = np.array([(3, 2.0), (1, 1.0), (2, 3.0)], dtype=dtype)
        f.create_dataset("/s/people", data=data)


# --- split_container_path (pure) -------------------------------------------


def test_split_container_path_h5():
    f = srv_utils.split_container_path
    assert f("@public/tree.h5/g/a") == (pathlib.Path("@public/tree.h5"), "/g/a")
    assert f("@public/sub/tree.hdf5/g") == (pathlib.Path("@public/sub/tree.hdf5"), "/g")
    # No descent: a plain .h5 (no inner key) still resolves to the container itself.
    assert f("@public/tree.h5") == (pathlib.Path("@public/tree.h5"), None)


# --- HTTP: list / info / fetch on a .h5 dropped into @public ----------------


@pytest.fixture
def fill_h5_public(client):
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = "test_tree.h5"
    _make_h5(dest_dir / fname)
    return fname, client.get(TEST_CATERVA2_ROOT)


def test_list_descends(fill_h5_public, client):
    fname, root = fill_h5_public
    # Root of the container: deep leaf listing, relative to the container path.
    assert client.get_list(f"{root.name}/{fname}") == ["g/a", "g/b", "h/c", "s/people"]
    # A group node lists only its leaves, relative to that node.
    assert client.get_list(f"{root.name}/{fname}/g") == ["a", "b"]


def test_info_leaf(fill_h5_public, client):
    fname, root = fill_h5_public
    info = client.get_info(f"{root.name}/{fname}/g/a")
    assert tuple(info["shape"]) == (2, 3)
    assert info["dtype"] == "int32"
    # A leaf has no file of its own; it inherits the container's mtime.
    assert info["mtime"] is not None


def test_info_bogus_inner_key_404(fill_h5_public, client):
    fname, root = fill_h5_public
    with pytest.raises(Exception) as e_info:
        client.get_info(f"{root.name}/{fname}/does/not/exist")
    assert "Not Found" in str(e_info.value)


def test_list_leaf_or_bogus_inner(fill_h5_public, client):
    """get_list on a leaf or bogus inner path degrades to [] (no 500)."""
    fname, root = fill_h5_public
    assert client.get_list(f"{root.name}/{fname}/g/a") == []
    assert client.get_list(f"{root.name}/{fname}/does/not/exist") == []


def test_remove_inner_member_404(fill_h5_public, auth_client):
    """Removing a member inside a container is a clean 404, not a 500."""
    if auth_client is None:
        pytest.skip("needs authentication")
    fname, root = fill_h5_public
    with pytest.raises(Exception) as e_info:
        auth_client.remove(f"{root.name}/{fname}/g")
    assert "Not Found" in str(e_info.value)


def test_client_ops_refuse_inner_member(fill_h5_public):
    """File-level ops (move/copy/remove/download/unfold) refuse container members."""
    fname, root = fill_h5_public
    grp = root[f"{fname}/g"]
    for op in (grp.remove, grp.unfold):
        with pytest.raises(ValueError, match="container"):
            op()
    with pytest.raises(ValueError, match="container"):
        grp.move("@public/elsewhere")
    leaf = root[f"{fname}/g/a"]
    with pytest.raises(ValueError, match="container"):
        leaf.download()
    # The container itself is a real file, so its path passes the guard.
    assert root[fname]._toplevel_path() is not None


def test_dir_named_like_container(client):
    """A real directory named *.h5 is not a container; files beneath it stay
    reachable (such directories are creatable through the upload API)."""
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public/dirname.h5"
    dest_dir.mkdir(parents=True, exist_ok=True)
    blosc2.asarray(np.arange(10, dtype="i4"), urlpath=str(dest_dir / "x.b2nd"), mode="w")
    root = client.get(TEST_CATERVA2_ROOT)
    assert "x.b2nd" in client.get_list(f"{root.name}/dirname.h5")
    info = client.get_info(f"{root.name}/dirname.h5/x.b2nd")
    assert tuple(info["shape"]) == (10,)


def test_fetch_leaf(fill_h5_public, client):
    fname, root = fill_h5_public
    whole = client.get_slice(f"{root.name}/{fname}/g/a", as_blosc2=True)
    assert np.array_equal(whole[:], np.arange(6, dtype="i4").reshape(2, 3))
    part = client.get_slice(f"{root.name}/{fname}/h/c", slice(2, 5), as_blosc2=True)
    assert np.array_equal(part[:], np.arange(10, dtype="i4")[2:5])


def test_leaf_size_not_container_size(fill_h5_public):
    """Each leaf reports its own size, not the whole container's (regression:
    the web listing used to show the container's file size for every leaf)."""
    fname, _root = fill_h5_public
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    abspath = dest_dir / fname
    container_size = abspath.stat().st_size

    container = srv_utils.open_container(abspath)
    try:
        size_a = container.leaf_size("/g/a")
        size_b = container.leaf_size("/g/b")
        size_c = container.leaf_size("/h/c")
    finally:
        container.close()

    assert None not in (size_a, size_b, size_c)
    assert len({size_a, size_b, size_c}) > 1  # not all identical
    assert container_size not in (size_a, size_b, size_c)


def test_web_virtual_root_leaf_sizes_differ(fill_h5_public, client):
    """The web listing shows distinct per-leaf sizes, not the container's."""
    fname, root = fill_h5_public
    base = client.urlbase
    r = httpx.get(f"{base}/htmx/path-list/", params={"roots": [f"{root.name}/{fname}"]})
    assert r.status_code == 200
    # /h/c (10 elements) is bigger on disk than /g/b (4 elements); if the bug
    # reappears both would render the same (the container's) formatted size.
    rows = r.text.split('class="input-group"')
    row_b = next(row for row in rows if f"{fname}/g/b" in row)
    row_c = next(row for row in rows if f"{fname}/h/c" in row)
    size_b = row_b.split('class="input-group-text" style="width: 20%">')[1].split("<")[0].strip()
    size_c = row_c.split('class="input-group-text" style="width: 20%">')[1].split("<")[0].strip()
    assert size_b != size_c


def test_client_get_mapping(fill_h5_public):
    from caterva2 import client as cc

    fname, root = fill_h5_public
    # The .h5 container itself is a browsable Group (not a Table).
    container = root[fname]
    assert isinstance(container, cc.Group)
    # Its children are addressed relative to the group.
    assert "g/a" in container
    # A virtual group inside the container is also a Group.
    grp = root[f"{fname}/g"]
    assert isinstance(grp, cc.Group)
    # A leaf resolves by server-reported kind (no file suffix to go on).
    leaf = root[f"{fname}/g/a"]
    assert isinstance(leaf, cc.Array)
    assert tuple(leaf.shape) == (2, 3)
    # Indexing through a group reaches the same leaf.
    assert isinstance(grp["a"], cc.Array)
    assert isinstance(container["g/a"], cc.Array)


def test_read_metadata_container_is_directory():
    """A plain .h5 presents as a Directory (the root group)."""
    import caterva2.models as models

    tmp = pathlib.Path(TEST_STATE_DIR) / "server/public"
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / "meta_tree.h5"
    _make_h5(p)
    meta = srv_utils.read_metadata(p)
    assert isinstance(meta, models.Directory)
    assert meta.kind == "group"
    assert meta.nfiles == 4
    assert meta.size > 0


def test_group_info(fill_h5_public, client):
    fname, root = fill_h5_public
    # info on a virtual group inside a .h5 container.
    ginfo = client.get_info(f"{root.name}/{fname}/g")
    assert ginfo["kind"] == "group"
    assert ginfo["nfiles"] == 2  # g/a, g/b
    assert ginfo["size"] > 0
    assert ginfo["mtime"] is not None


def test_web_no_500(fill_h5_public, client):
    """Web path-info must not 500 on a .h5 container or its leaves."""
    fname, root = fill_h5_public
    base = client.urlbase
    r = httpx.get(f"{base}/htmx/path-info/{root.name}/{fname}")
    assert r.status_code == 200
    r = httpx.get(f"{base}/htmx/path-info/{root.name}/{fname}/g/a")
    assert r.status_code == 200


def test_web_container_is_single_row(fill_h5_public, client):
    """A classic root shows the .h5 as one row, not expanded into leaves."""
    fname, root = fill_h5_public
    base = client.urlbase
    r = httpx.get(f"{base}/htmx/path-list/", params={"roots": [root.name]})
    assert r.status_code == 200
    assert f"{root.name}/{fname}" in r.text  # the container row
    assert f"{fname}/g/a" not in r.text  # leaves are not auto-expanded


def test_web_virtual_root_expands_leaves(fill_h5_public, client):
    """Mounting the container as a virtual root lists its leaves."""
    fname, root = fill_h5_public
    base = client.urlbase
    r = httpx.get(f"{base}/htmx/path-list/", params={"roots": [f"{root.name}/{fname}"]})
    assert r.status_code == 200
    assert f"{root.name}/{fname}/g/a" in r.text
    assert f"{root.name}/{fname}/h/c" in r.text


def test_web_leaf_view_no_500(fill_h5_public, client):
    """Clicking a mounted .h5 leaf renders data (was: crashed via blosc2.open)."""
    fname, root = fill_h5_public
    base = client.urlbase
    r = httpx.post(f"{base}/htmx/path-view/{root.name}/{fname}/g/a")
    assert r.status_code == 200


def test_web_leaf_view_filter_unsupported(fill_h5_public, client):
    """Filter on HDF5 members still gives friendly 400 (LazyExpr not plumbed)."""
    fname, root = fill_h5_public
    base = client.urlbase
    r = httpx.post(f"{base}/htmx/path-view/{root.name}/{fname}/s/people", data={"filter": "x > 1"})
    assert r.status_code == 400
    assert "not supported" in r.text


def test_fetch_h5_member_filter_unsupported(fill_h5_public, client):
    """/api/fetch with a filter on an HDF5 member gives a 400, not a 500 or
    silently unfiltered data."""
    fname, root = fill_h5_public
    r = httpx.get(f"{client.urlbase}/api/fetch/{root.name}/{fname}/s/people", params={"filter": "x > 1"})
    assert r.status_code == 400
    assert "not supported" in r.text


def test_web_leaf_view_sort_i4_no_fields(fill_h5_public, client):
    """Sort on a plain (no fields) HDF5 leaf = friendly 400, not 500."""
    fname, root = fill_h5_public
    base = client.urlbase
    r = httpx.post(f"{base}/htmx/path-view/{root.name}/{fname}/g/a", data={"sortby": "x"})
    assert r.status_code == 400
    assert "not supported" in r.text


# --- Phase 2: sort on HDF5 structured members ---------------------------------


def test_htmx_path_view_h5_member_sort_asc(fill_h5_public, client):
    """Sort a structured HDF5 leaf ascending."""
    fname, root = fill_h5_public
    base = client.urlbase
    resp = httpx.post(f"{base}/htmx/path-view/{root.name}/{fname}/s/people", data={"sortby": "x"})
    assert resp.status_code == 200
    # Data: (3,2.0), (1,1.0), (2,3.0) → ascending by x: 1, 2, 3
    pos1 = resp.text.index("<td>1</td>")
    pos2 = resp.text.index("<td>2</td>")
    pos3 = resp.text.index("<td>3</td>")
    assert pos1 < pos2 < pos3
    assert "&#9650;" in resp.text


def test_htmx_path_view_h5_member_sort_desc(fill_h5_public, client):
    """Sort a structured HDF5 leaf descending."""
    fname, root = fill_h5_public
    base = client.urlbase
    resp = httpx.post(
        f"{base}/htmx/path-view/{root.name}/{fname}/s/people",
        data={"sortby": "x", "sortdir": "desc"},
    )
    assert resp.status_code == 200
    # Descending by x: 3, 2, 1
    pos1 = resp.text.index("<td>1</td>")
    pos2 = resp.text.index("<td>2</td>")
    pos3 = resp.text.index("<td>3</td>")
    assert pos3 < pos2 < pos1
    assert "&#9660;" in resp.text


def test_htmx_path_view_h5_member_filterable_false(fill_h5_public, client):
    """HDF5 member views must hide the filter box (filterable=False in context)."""
    fname, root = fill_h5_public
    base = client.urlbase
    resp = httpx.post(f"{base}/htmx/path-view/{root.name}/{fname}/s/people")
    assert resp.status_code == 200
    assert 'name="filter"' not in resp.text


def test_web_bogus_h5_no_500(client):
    """A non-HDF5/corrupt .h5 (classic walk or mounted) must not 500."""
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "bogus.h5").write_text("not an hdf5 file")
    root = client.get(TEST_CATERVA2_ROOT)
    base = client.urlbase
    # Classic listing walks the bogus file.
    r = httpx.get(f"{base}/htmx/path-list/", params={"roots": [root.name]})
    assert r.status_code == 200
    # Mounting it as a virtual root just lists nothing, no crash.
    r = httpx.get(f"{base}/htmx/path-list/", params={"roots": [f"{root.name}/bogus.h5"]})
    assert r.status_code == 200


# --- api/chunk on an HDF5 leaf ---------------------------------------------


def test_chunk_of_an_hdf5_leaf_is_refused(fill_h5_public, client):
    """An HDF5 dataset is HDF5-compressed, so a Blosc2 chunk of one would have to
    be read and recompressed per request. The endpoint says so instead, and
    api/fetch with a slice_ computes exactly the region wanted."""
    fname, _root = fill_h5_public
    for path in (f"{fname}/g/a", fname):  # a leaf, and the file itself
        response = httpx.get(f"{client.urlbase}/api/chunk/{TEST_CATERVA2_ROOT}/{path}?nchunk=0")
        assert response.status_code == 400
        assert "slice_" in response.json()["detail"]
