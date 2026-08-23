"""Tests for TreeStore .b2z virtual descent (list/info/fetch of inner leaves)."""

import pathlib

import blosc2
import httpx
import numpy as np
import pytest

from caterva2.services import srv_utils

from .services import TEST_CATERVA2_ROOT, TEST_STATE_DIR


def _make_tree(path):
    t = blosc2.TreeStore(str(path), mode="w")
    t["/g/a"] = np.arange(6, dtype="i4").reshape(2, 3)
    t["/g/b"] = np.arange(4, dtype="i4")
    t["/h/c"] = np.arange(10, dtype="i4")
    # Structured leaf for filter/sort tests
    t["/s/people"] = np.array([(1, 3.0), (2, 1.0), (3, 2.0)], dtype=[("x", "i4"), ("y", "f8")])
    # 0-d leaf for scalar rendering
    t["/s/scalar"] = np.array(42, dtype="i4")
    t.close()


# --- split_container_path (pure) -------------------------------------------


def test_split_container_path():
    f = srv_utils.split_container_path
    assert f("@public/tree.b2z/g/a") == (pathlib.Path("@public/tree.b2z"), "/g/a")
    assert f("@public/sub/tree.b2z/g") == (pathlib.Path("@public/sub/tree.b2z"), "/g")
    # No descent: plain container or plain file.
    assert f("@public/tree.b2z") == (pathlib.Path("@public/tree.b2z"), None)
    assert f("@public/dir/x.b2nd") == (pathlib.Path("@public/dir/x.b2nd"), None)


# --- HTTP: list / info / fetch on a TreeStore dropped into @public ----------


@pytest.fixture
def fill_tree_public(client):
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = "test_tree.b2z"
    _make_tree(dest_dir / fname)
    return fname, client.get(TEST_CATERVA2_ROOT)


def test_list_descends(fill_tree_public, client):
    fname, root = fill_tree_public
    # Root of the container: deep leaf listing, relative to the container path.
    assert client.get_list(f"{root.name}/{fname}") == ["g/a", "g/b", "h/c", "s/people", "s/scalar"]
    # A group node lists only its leaves, relative to that node.
    assert client.get_list(f"{root.name}/{fname}/g") == ["a", "b"]


def test_info_leaf(fill_tree_public, client):
    fname, root = fill_tree_public
    info = client.get_info(f"{root.name}/{fname}/g/a")
    assert tuple(info["shape"]) == (2, 3)
    assert info["dtype"] == "int32"
    # A leaf has no file of its own; it inherits the container's mtime.
    assert info["mtime"] is not None


def test_fetch_leaf(fill_tree_public, client):
    fname, root = fill_tree_public
    whole = client.get_slice(f"{root.name}/{fname}/g/a", as_blosc2=True)
    assert np.array_equal(whole[:], np.arange(6, dtype="i4").reshape(2, 3))
    part = client.get_slice(f"{root.name}/{fname}/h/c", slice(2, 5), as_blosc2=True)
    assert np.array_equal(part[:], np.arange(10, dtype="i4")[2:5])


def test_leaf_size_not_container_size(fill_tree_public):
    """Each leaf reports its own size, not the whole container's (regression:
    the web listing used to show the container's file size for every leaf)."""
    fname, _root = fill_tree_public
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


def test_client_get_mapping(fill_tree_public):
    from caterva2 import client as cc

    fname, root = fill_tree_public
    # The TreeStore container itself is a browsable Group (not a Table).
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
    """A TreeStore .b2z presents as a Directory (the root group)."""
    import caterva2.models as models

    tmp = pathlib.Path(TEST_STATE_DIR) / "server/public"
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / "meta_tree.b2z"
    _make_tree(p)
    meta = srv_utils.read_metadata(p)
    assert isinstance(meta, models.Directory)
    assert meta.kind == "group"
    assert meta.nfiles == 5
    assert meta.size > 0


def test_dir_and_group_info(fill_tree_public, client):
    from caterva2 import client as cc

    fname, root = fill_tree_public
    # A real directory with two datasets.
    ddir = pathlib.Path(TEST_STATE_DIR) / "server/public" / "gdir"
    ddir.mkdir(parents=True, exist_ok=True)
    blosc2.asarray(np.arange(4), urlpath=str(ddir / "x.b2nd"), mode="w")
    blosc2.asarray(np.arange(6), urlpath=str(ddir / "y.b2nd"), mode="w")

    # info on a real directory returns a dir summary.
    dinfo = client.get_info(f"{root.name}/gdir")
    assert dinfo["kind"] == "group"
    assert dinfo["nfiles"] == 2
    assert dinfo["size"] > 0
    # a real directory maps to a Group client-side, browsable/indexable.
    grp = root["gdir"]
    assert isinstance(grp, cc.Group)
    assert "x.b2nd" in grp
    assert isinstance(grp["x.b2nd"], cc.Array)
    # info on a virtual group inside a .b2z container.
    ginfo = client.get_info(f"{root.name}/{fname}/g")
    assert ginfo["kind"] == "group"
    assert ginfo["nfiles"] == 2  # g/a, g/b
    assert ginfo["size"] > 0  # summed from the .b2z zip index
    assert ginfo["mtime"] is not None


@pytest.fixture
def fill_dict_public(client):
    """A `.b2z` written through the flat DictStore API, which stays one.

    It has no tree: its keys are paths, so a group in it is a prefix that some
    key continues rather than an object of its own.
    """
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = "test_dict.b2z"
    store = blosc2.DictStore(str(dest_dir / fname), mode="w")
    store["/flat"] = blosc2.asarray(np.arange(7))
    store["/grp/nested"] = blosc2.asarray(np.arange(30))
    store.close()
    return fname, client.get(TEST_CATERVA2_ROOT)


def test_a_dictstore_prefix_is_a_group(fill_dict_public, client):
    """What the listing hands out, `api/info` has to answer for.

    A DictStore has no object for a group, so a prefix used to resolve to
    nothing and 404 -- for a path the listing itself named.
    """
    fname, root = fill_dict_public
    assert client.get_list(f"{root.name}/{fname}") == ["flat", "grp/nested"]
    info = client.get_info(f"{root.name}/{fname}/grp")
    assert info["kind"] == "group"
    assert info["nfiles"] == 1
    assert info["size"] > 0
    # ...and a leaf is still a leaf, a missing key still missing
    assert tuple(client.get_info(f"{root.name}/{fname}/flat")["shape"]) == (7,)
    response = httpx.get(f"{client.urlbase}/api/info/{root.name}/{fname}/nope")
    assert response.status_code == 404


def test_a_container_refuses_coordinates(fill_tree_public, client):
    """A container is served as the file it is, so there is nothing to narrow.

    `slice_` was refused here and `indices` was not, so a fetch naming two
    coordinates got the whole container file back with a 200.
    """
    fname, root = fill_tree_public
    url = f"{client.urlbase}/api/fetch/{root.name}/{fname}"
    response = httpx.get(url, params={"indices": "[[1,2]]"})
    assert response.status_code == 400
    assert "nothing to slice" in response.json()["detail"]
    # ...as `slice_` already was, and the whole of it still comes back unasked
    assert httpx.get(url, params={"slice_": "0:1"}).status_code == 400
    assert httpx.get(url).status_code == 200


def test_malformed_inner_key_404(fill_tree_public, client):
    """Malformed TreeStore keys (e.g. NUL bytes survive URL decoding) raise
    ValueError inside blosc2; they must 404, not 500."""
    fname, root = fill_tree_public
    base = client.urlbase
    r = httpx.get(f"{base}/api/info/{root.name}/{fname}/g/%00bad")
    assert r.status_code == 404


def test_web_no_500(fill_tree_public, client):
    """Web path-info must not 500 on a TreeStore container or its leaves."""
    fname, root = fill_tree_public
    base = client.urlbase
    r = httpx.get(f"{base}/htmx/path-info/{root.name}/{fname}")
    assert r.status_code == 200
    r = httpx.get(f"{base}/htmx/path-info/{root.name}/{fname}/g/a")
    assert r.status_code == 200


def test_web_container_is_single_row(fill_tree_public, client):
    """A classic root shows the .b2z as one row, not expanded into leaves."""
    fname, root = fill_tree_public
    base = client.urlbase
    r = httpx.get(f"{base}/htmx/path-list/", params={"roots": [root.name]})
    assert r.status_code == 200
    assert f"{root.name}/{fname}" in r.text  # the container row
    assert f"{fname}/g/a" not in r.text  # leaves are not auto-expanded


def test_web_virtual_root_expands_leaves(fill_tree_public, client):
    """Mounting the container as a virtual root lists its leaves."""
    fname, root = fill_tree_public
    base = client.urlbase
    r = httpx.get(f"{base}/htmx/path-list/", params={"roots": [f"{root.name}/{fname}"]})
    assert r.status_code == 200
    assert f"{root.name}/{fname}/g/a" in r.text
    assert f"{root.name}/{fname}/h/c" in r.text


def test_web_bogus_container_no_500(client):
    """A non-TreeStore/corrupt .b2z (classic walk or mounted) must not 500."""
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "bogus.b2z").write_text("not a blosc2 container")
    root = client.get(TEST_CATERVA2_ROOT)
    base = client.urlbase
    # Classic listing walks the bogus file.
    r = httpx.get(f"{base}/htmx/path-list/", params={"roots": [root.name]})
    assert r.status_code == 200
    # Mounting it as a virtual root just lists nothing, no crash.
    r = httpx.get(f"{base}/htmx/path-list/", params={"roots": [f"{root.name}/bogus.b2z"]})
    assert r.status_code == 200


# --- Phase 1: filter/sort on .b2z structured members -----------------------


def test_htmx_path_view_member_sort_asc(fill_tree_public, client):
    """Sort a structured TreeStore leaf by field ascending."""
    fname, root = fill_tree_public
    base = client.urlbase
    resp = httpx.post(f"{base}/htmx/path-view/{root.name}/{fname}/s/people", data={"sortby": "x"})
    assert resp.status_code == 200
    # x values: 1 before 2 before 3
    pos1 = resp.text.index("<td>1</td>")
    pos2 = resp.text.index("<td>2</td>")
    pos3 = resp.text.index("<td>3</td>")
    assert pos1 < pos2 < pos3
    assert "&#9650;" in resp.text  # asc indicator


def test_htmx_path_view_member_sort_desc(fill_tree_public, client):
    """Sort a structured TreeStore leaf by field descending."""
    fname, root = fill_tree_public
    base = client.urlbase
    resp = httpx.post(
        f"{base}/htmx/path-view/{root.name}/{fname}/s/people",
        data={"sortby": "x", "sortdir": "desc"},
    )
    assert resp.status_code == 200
    # x values: 3 before 2 before 1
    pos1 = resp.text.index("<td>1</td>")
    pos2 = resp.text.index("<td>2</td>")
    pos3 = resp.text.index("<td>3</td>")
    assert pos3 < pos2 < pos1
    assert "&#9660;" in resp.text  # desc indicator


def test_htmx_path_view_member_filter_only(fill_tree_public, client):
    """Filter without sort on a structured TreeStore leaf (regression: blosc2's
    where fastpath re-opened the leaf's urlpath — the whole .b2z — and crashed)."""
    fname, root = fill_tree_public
    base = client.urlbase
    resp = httpx.post(f"{base}/htmx/path-view/{root.name}/{fname}/s/people", data={"filter": "x > 1"})
    assert resp.status_code == 200
    assert "<td>2</td>" in resp.text
    assert "<td>3</td>" in resp.text
    assert "<td>1</td>" not in resp.text  # the x == 1 row is filtered out


def test_htmx_path_view_member_filter_and_sort(fill_tree_public, client):
    """Filter combined with sort on a structured TreeStore leaf."""
    fname, root = fill_tree_public
    base = client.urlbase
    resp = httpx.post(
        f"{base}/htmx/path-view/{root.name}/{fname}/s/people",
        data={"filter": "x > 1", "sortby": "y", "sortdir": "desc"},
    )
    assert resp.status_code == 200
    # Rows with x > 1 sorted by y descending: (3, y=2.0) before (2, y=1.0).
    assert resp.text.index("<td>3</td>") < resp.text.index("<td>2</td>")


def test_fetch_member_filter(fill_tree_public, client):
    """/api/fetch must honor the filter parameter on container members
    (regression: it was silently ignored and the whole member returned)."""
    fname, root = fill_tree_public
    arr = client.get_slice(f"{root.name}/{fname}/s/people", "x > 1")
    assert arr[:]["x"].tolist() == [2, 3]


def test_htmx_path_view_bogus_member_sort_friendly_error(client):
    """Sorting a member path inside a non-TreeStore .b2z gives a clear 400
    (regression: AttributeError surfaced as a bogus 'Invalid filter' message)."""
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "server/public"
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "bogus2.b2z").write_text("not a blosc2 container")
    root = client.get(TEST_CATERVA2_ROOT)
    resp = httpx.post(f"{client.urlbase}/htmx/path-view/{root.name}/bogus2.b2z/x", data={"sortby": "x"})
    assert resp.status_code == 400
    assert "Cannot open container member" in resp.text


def test_htmx_path_view_member_i4_no_fields(fill_tree_public, client):
    """Sort/filter on a plain (no fields) TreeStore leaf gives friendly 400, not 500."""
    fname, root = fill_tree_public
    base = client.urlbase
    for data in ({"sortby": "x"}, {"filter": "x > 0"}):
        resp = httpx.post(f"{base}/htmx/path-view/{root.name}/{fname}/g/a", data=data)
        assert resp.status_code == 400
        assert "not supported" in resp.text


def test_htmx_path_view_member_0d_scalar(fill_tree_public, client):
    """0-d container member must render without 500 (unhashable np.array regression)."""
    fname, root = fill_tree_public
    base = client.urlbase
    resp = httpx.post(f"{base}/htmx/path-view/{root.name}/{fname}/s/scalar")
    assert resp.status_code == 200
    assert "42" in resp.text


# --- api/chunk on a container leaf -----------------------------------------


def _chunk_url(client, path, nchunk):
    return f"{client.urlbase}/api/chunk/{TEST_CATERVA2_ROOT}/{path}?nchunk={nchunk}"


def test_chunk_of_a_leaf_is_the_stored_one(fill_tree_public, client):
    """A TreeStore keeps its leaves as ordinary Blosc2 arrays, so a chunk of one
    is served as it lies in the .b2z -- nothing is sliced or recompressed."""
    fname, _root = fill_tree_public
    response = httpx.get(_chunk_url(client, f"{fname}/g/a", 0))
    assert response.status_code == 200

    stored = blosc2.open(pathlib.Path(TEST_STATE_DIR) / "server/public" / fname)["/g/a"]
    assert response.content == stored.schunk.get_chunk(0)


def test_proxy_over_a_leaf_reads_it(fill_tree_public, client):
    """What the chunk endpoint is for: a Proxy caching a remote array chunk by
    chunk. Before api/chunk resolved container paths, this 404ed."""
    fname, _root = fill_tree_public
    remote = blosc2.C2Array(f"{TEST_CATERVA2_ROOT}/{fname}/g/a", urlbase=client.urlbase)
    proxy = blosc2.Proxy(remote, mode="w")
    assert np.array_equal(proxy[:], np.arange(6, dtype="i4").reshape(2, 3))


def test_chunk_of_a_missing_leaf(fill_tree_public, client):
    fname, _root = fill_tree_public
    assert httpx.get(_chunk_url(client, f"{fname}/g/nope", 0)).status_code == 404
    # A group is not a dataset either
    assert httpx.get(_chunk_url(client, f"{fname}/g", 0)).status_code == 404


def test_chunk_of_a_ctable_is_refused(client):
    """A CTable is a set of columns rather than one chunked array: it has no
    schunk to take a chunk out of, and used to fail as a 500."""
    from .test_ctable import _make_table

    fname = "test_chunk_ctable.b2z"
    dest = pathlib.Path(TEST_STATE_DIR) / "server/public" / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    _make_table(dest)

    response = httpx.get(_chunk_url(client, fname, 0))
    assert response.status_code == 400
    assert "slice_" in response.json()["detail"]


def test_the_opened_leaf_is_kept_between_requests(fill_tree_public):
    """Reading a leaf chunk by chunk must not reopen the container per chunk;
    a container rewritten underneath must not be served from the cache either."""
    from caterva2.services.server import open_member

    fname, _root = fill_tree_public
    abspath = pathlib.Path(TEST_STATE_DIR) / "server/public" / fname
    mtime = abspath.stat().st_mtime

    leaf = open_member(abspath, "/g/a", mtime)
    assert open_member(abspath, "/g/a", mtime) is leaf
    assert open_member(abspath, "/g/b", mtime) is not leaf
    assert open_member(abspath, "/g/a", mtime + 1) is not leaf  # rewritten since


# --- byte ranges into a leaf's frame ----------------------------------------


def _fetch_url(client, path):
    return f"{client.urlbase}/api/fetch/{TEST_CATERVA2_ROOT}/{path}"


def _window(path, key):
    store = blosc2.open(str(path))
    return store.member_window(key)


def test_a_leaf_is_served_from_its_window(fill_tree_public, client):
    """A .b2z keeps each leaf as a stored zip member, so the leaf's frame is in
    the file and the whole of it can be served by seeking to it."""
    fname, _root = fill_tree_public
    abspath = pathlib.Path(TEST_STATE_DIR) / "server/public" / fname
    offset, nbytes = _window(abspath, "/g/a")

    response = httpx.get(_fetch_url(client, f"{fname}/g/a"))
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == abspath.read_bytes()[offset : offset + nbytes]
    assert np.array_equal(
        blosc2.ndarray_from_cframe(response.content)[:], np.arange(6, dtype="i4").reshape(2, 3)
    )


def test_a_leaf_keeps_the_partitioning_info_reports(fill_tree_public, client):
    """The rebuild this replaced re-sliced and recompressed, so what came back
    disagreed with the chunks and blocks api/info reports for the same leaf."""
    fname, _root = fill_tree_public
    dest = pathlib.Path(TEST_STATE_DIR) / "server/public" / fname
    with blosc2.TreeStore(str(dest), mode="a") as tstore:
        tstore["/chunked"] = blosc2.asarray(
            np.arange(4000, dtype="i4").reshape(200, 20), chunks=(50, 20), blocks=(10, 20)
        )

    fetched = blosc2.ndarray_from_cframe(httpx.get(_fetch_url(client, f"{fname}/chunked")).content)
    info = httpx.get(f"{client.urlbase}/api/info/{TEST_CATERVA2_ROOT}/{fname}/chunked").json()
    assert fetched.chunks == tuple(info["chunks"]) == (50, 20)
    assert fetched.blocks == tuple(info["blocks"]) == (10, 20)


def test_ranges_of_a_leaf(fill_tree_public, client):
    fname, _root = fill_tree_public
    abspath = pathlib.Path(TEST_STATE_DIR) / "server/public" / fname
    offset, nbytes = _window(abspath, "/g/a")
    raw = abspath.read_bytes()

    response = httpx.get(_fetch_url(client, f"{fname}/g/a"), headers={"Range": "bytes=0-31"})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-31/{nbytes}"
    assert response.content == raw[offset : offset + 32]
    # ... and the leaf's byte 0 is the frame's, not the container's
    assert response.content[2:9] == b"b2frame"


def test_a_range_cannot_reach_past_the_leaf(fill_tree_public, client):
    # The window is the whole of what this path is about: a range that would
    # run into the next member is clamped, and one that starts beyond it is a 416
    fname, _root = fill_tree_public
    abspath = pathlib.Path(TEST_STATE_DIR) / "server/public" / fname
    _offset, nbytes = _window(abspath, "/g/a")

    clamped = httpx.get(_fetch_url(client, f"{fname}/g/a"), headers={"Range": f"bytes=0-{nbytes + 999}"})
    assert clamped.status_code == 206
    assert clamped.headers["content-range"] == f"bytes 0-{nbytes - 1}/{nbytes}"
    assert len(clamped.content) == nbytes

    beyond = httpx.get(_fetch_url(client, f"{fname}/g/a"), headers={"Range": f"bytes={nbytes}-{nbytes + 9}"})
    assert beyond.status_code == 416
    assert beyond.headers["content-range"] == f"bytes */{nbytes}"


def test_several_ranges_of_a_leaf_come_back_multipart(fill_tree_public, client):
    fname, _root = fill_tree_public
    abspath = pathlib.Path(TEST_STATE_DIR) / "server/public" / fname
    offset, _nbytes = _window(abspath, "/g/a")
    raw = abspath.read_bytes()

    response = httpx.get(_fetch_url(client, f"{fname}/g/a"), headers={"Range": "bytes=0-15, 40-55"})
    assert response.status_code == 206
    assert response.headers["content-type"].startswith("multipart/byteranges")
    assert raw[offset : offset + 16] in response.content
    assert raw[offset + 40 : offset + 56] in response.content


def test_a_leaf_s_ranges_carry_the_container_s_validator(fill_tree_public, client):
    """The two-request read is what the validator is for, leaves included.

    A client reads a leaf's frame header in one ranged request and its chunk
    offsets in another; without an `ETag` on either it cannot tell that the
    container was not rewritten in between, which is exactly when the bytes it
    reads as offsets are a chunk's.  `api/info` reports the same one, so the
    client has something to compare against.
    """
    fname, root = fill_tree_public
    info = httpx.get(f"{client.urlbase}/api/info/{root.name}/{fname}/g/a")
    assert info.status_code == 200
    etag = info.headers["etag"]
    assert etag

    ranged = httpx.get(_fetch_url(client, f"{fname}/g/a"), headers={"Range": "bytes=0-31"})
    assert ranged.status_code == 206
    assert ranged.headers["etag"] == etag
    multi = httpx.get(_fetch_url(client, f"{fname}/g/a"), headers={"Range": "bytes=0-15, 40-55"})
    assert multi.status_code == 206
    assert multi.headers["etag"] == etag
    whole = httpx.get(_fetch_url(client, f"{fname}/g/a"))
    assert whole.status_code == 200
    assert whole.headers["etag"] == etag


def test_a_sliced_leaf_still_refuses_ranges(fill_tree_public, client):
    # A slice is computed per request whatever it is a slice of
    fname, _root = fill_tree_public
    response = httpx.get(
        _fetch_url(client, f"{fname}/h/c"), params={"slice_": "2:5"}, headers={"Range": "bytes=0-31"}
    )
    assert response.status_code == 416


def test_a_container_is_served_whole(fill_tree_public, client):
    """A container is a file of leaves rather than an array; asking for it used
    to die in the type ladder, on a typesize a TreeStore has not got."""
    fname, _root = fill_tree_public
    abspath = pathlib.Path(TEST_STATE_DIR) / "server/public" / fname

    response = httpx.get(_fetch_url(client, fname))
    assert response.status_code == 200
    assert response.content == abspath.read_bytes()
    assert httpx.get(_fetch_url(client, fname), params={"slice_": "0:2"}).status_code == 400
