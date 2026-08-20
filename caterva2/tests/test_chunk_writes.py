###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
"""Filling one array from several writers, a chunk at a time.

The array is laid out first and never resized: `blosc2.uninit` writes a couple
of hundred bytes whatever the array's size, and every chunk of it is a slot
nothing has been written to.  A writer claims a slot by writing it, and the
second writer to reach the same slot is refused -- so the coordination lives in
the array's own offsets rather than in anything the writers hold between them.
"""

import concurrent.futures
import pathlib
import time

import blosc2
import httpx
import numpy as np
import pytest

from .services import TEST_STATE_DIR

CHUNKS = (1000,)
BLOCKS = (250,)
NCHUNKS = 5
SHAPE = (CHUNKS[0] * NCHUNKS,)
DTYPE = np.dtype(np.int32)


def _chunk(value):
    """A chunk of the array's geometry, filled with *value*."""
    data = np.full(CHUNKS, value, dtype=DTYPE)
    return blosc2.compress2(data, typesize=DTYPE.itemsize, blocksize=BLOCKS[0] * DTYPE.itemsize)


@pytest.fixture
def presized(auth_client, tmp_path):
    """A pre-sized array in the user's personal area, and a C2Array over it."""
    if auth_client is None:
        pytest.skip("writing chunks requires an authenticated user")
    local = tmp_path / "run.b2nd"
    array = blosc2.uninit(SHAPE, dtype=DTYPE, chunks=CHUNKS, blocks=BLOCKS, urlpath=str(local))
    del array  # the server's handle is to be the only one over the uploaded copy
    assert local.stat().st_size < 1024  # laid out, not allocated
    remote = auth_client.upload(str(local), "@personal/run.b2nd")
    return blosc2.C2Array(str(remote), urlbase=auth_client.urlbase, auth_token=auth_client.cookie)


def test_a_chunk_lands_in_a_pre_sized_array(presized):
    presized.update_chunk(2, _chunk(7))
    np.testing.assert_array_equal(presized[2 * CHUNKS[0] : 3 * CHUNKS[0]], np.full(CHUNKS, 7, DTYPE))


def test_a_write_says_how_far_the_fill_has_got(presized):
    assert presized.update_chunk(0, _chunk(1)) == {
        "nchunk": 0,
        "written": 1,
        "nchunks": NCHUNKS,
        "state": "filling",
    }
    assert presized.update_chunk(3, _chunk(1))["written"] == 2


def test_a_second_write_to_a_slot_is_refused(presized):
    presized.update_chunk(1, _chunk(11))
    with pytest.raises(blosc2.ChunkAlreadyWritten):
        presized.update_chunk(1, _chunk(22))
    # The array is untouched: the first write is what stands
    np.testing.assert_array_equal(presized[CHUNKS[0] : 2 * CHUNKS[0]], np.full(CHUNKS, 11, DTYPE))


def test_a_slot_written_with_zeros_is_written(presized):
    """Why an array to be filled is laid out with `uninit` and not with `zeros`.

    An all-zero chunk compresses to a run of zeros, which is special again -- but
    tagged as zeros rather than as uninitialized, so the slot is still tellable
    from one nobody has reached.
    """
    presized.update_chunk(4, _chunk(0))
    assert presized.written_chunks()[4]
    with pytest.raises(blosc2.ChunkAlreadyWritten):
        presized.update_chunk(4, _chunk(5))


def test_a_chunk_of_another_geometry_is_refused(presized):
    half = np.zeros(CHUNKS[0] // 2, dtype=DTYPE)
    wrong = blosc2.compress2(half, typesize=DTYPE.itemsize)
    with pytest.raises(httpx.HTTPStatusError) as raised:
        presized.update_chunk(0, wrong)
    assert raised.value.response.status_code == 400
    assert not presized.written_chunks().any()


def test_a_chunk_of_another_blocksize_is_refused(presized):
    data = np.full(CHUNKS, 3, dtype=DTYPE)
    wrong = blosc2.compress2(data, typesize=DTYPE.itemsize)  # blosc2 picks the blocksize
    with pytest.raises(httpx.HTTPStatusError) as raised:
        presized.update_chunk(0, wrong)
    assert raised.value.response.status_code == 400


def test_writing_a_chunk_needs_authentication(presized, sub_user):
    """Posted without the cookie, rather than through a `C2Array`.

    An anonymous client cannot even open a personal dataset -- `api/info` is
    refused first -- so the write is what has to be tried directly.
    """
    if not sub_user:
        pytest.skip("no authentication configured")
    url = f"{presized.urlbase}api/chunk/{presized.path}"
    response = httpx.post(url, params={"nchunk": 0}, content=_chunk(1))
    assert response.status_code in (401, 403)
    # ... and nothing was written
    assert not presized.written_chunks().any()


def test_the_fill_is_legible_from_the_offsets(presized):
    assert list(presized.written_chunks()) == [False] * NCHUNKS
    presized.update_chunk(3, _chunk(3))
    assert list(presized.written_chunks()) == [False, False, False, True, False]


def test_several_writers_fill_one_array(presized):
    path, urlbase, token = presized.path, presized.urlbase, presized.auth_token

    def fill(nchunk):
        writer = blosc2.C2Array(path, urlbase=urlbase, auth_token=token)
        writer.update_chunk(nchunk, _chunk(nchunk))
        return nchunk

    with concurrent.futures.ThreadPoolExecutor(max_workers=NCHUNKS) as pool:
        assert sorted(pool.map(fill, range(NCHUNKS))) == list(range(NCHUNKS))

    assert presized.written_chunks().all()
    expected = np.repeat(np.arange(NCHUNKS, dtype=DTYPE), CHUNKS[0])
    np.testing.assert_array_equal(presized[:], expected)


def test_two_writers_racing_for_a_slot_leave_one_winner(presized):
    path, urlbase, token = presized.path, presized.urlbase, presized.auth_token

    def fill(value):
        writer = blosc2.C2Array(path, urlbase=urlbase, auth_token=token)
        try:
            writer.update_chunk(2, _chunk(value))
            return "won"
        except blosc2.ChunkAlreadyWritten:
            return "lost"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(fill, (8, 9))) == ["lost", "won"]
    stored = np.unique(presized[2 * CHUNKS[0] : 3 * CHUNKS[0]])
    assert len(stored) == 1
    assert stored[0] in (8, 9)


def _etag(array):
    url = f"{array.urlbase}api/info/{array.path}"
    response = httpx.get(url, headers={"Cookie": array.auth_token})
    response.raise_for_status()
    return response.headers.get("etag")


def test_a_write_changes_the_etag_even_when_the_file_does_not_grow(presized):
    """The reason the validator is the frame's generation counter and not its size.

    A chunk stored as a run of zeros has no payload at all, so the frame can come
    out of the write exactly as long as it went in.  A validator built from the
    length would not move, and a client would splice the bytes of a chunk where
    it expected the offsets it had cached.
    """
    before = _etag(presized)
    assert before is not None
    presized.update_chunk(0, _chunk(0))  # a run of zeros: no payload stored
    first = _etag(presized)
    assert first != before
    presized.update_chunk(1, _chunk(0))  # and another, in case the offsets held their size
    assert _etag(presized) != first


def test_the_etag_is_the_one_the_ranged_reads_carry(presized):
    presized.update_chunk(0, _chunk(1))
    url = f"{presized.urlbase}api/fetch/{presized.path}"
    ranged = httpx.get(url, headers={"Cookie": presized.auth_token, "Range": "bytes=0-31"})
    assert ranged.status_code == 206
    assert ranged.headers["etag"] == _etag(presized)


def test_a_dataset_that_is_not_an_array_is_refused(auth_client, tmp_path):
    """Only a stored .b2nd has chunks of a geometry to write into."""
    if auth_client is None:
        pytest.skip("writing chunks requires an authenticated user")
    local = pathlib.Path(tmp_path) / "notes.txt"
    local.write_text("not a frame at all")
    auth_client.upload(str(local), "@personal/notes.txt")
    # A plain file is stored compressed, under a suffix of its own
    url = f"{auth_client.urlbase}/api/chunk/@personal/notes.txt.b2"
    response = httpx.post(url, params={"nchunk": 0}, content=b"x", headers={"Cookie": auth_client.cookie})
    assert response.status_code == 400


def test_an_array_that_does_not_exist_is_not_created(auth_client):
    """A fill writes into a layout; it does not invent one."""
    if auth_client is None:
        pytest.skip("writing chunks requires an authenticated user")
    url = f"{auth_client.urlbase}/api/chunk/@personal/never-made.b2nd"
    response = httpx.post(
        url, params={"nchunk": 0}, content=_chunk(1), headers={"Cookie": auth_client.cookie}
    )
    assert response.status_code == 404


def test_a_chunk_out_of_range_is_refused(presized):
    with pytest.raises(httpx.HTTPStatusError) as raised:
        presized.update_chunk(NCHUNKS + 3, _chunk(1))
    assert raised.value.response.status_code == 404


def _published_dir():
    return pathlib.Path(TEST_STATE_DIR) / "published"


def test_a_filled_array_is_published_by_itself(presized):
    """The handover: Caterva2 is written to a chunk at a time, and what leaves is
    one finished frame -- which is what a byte-range reader wants of an object
    store, and none of what writing chunks to one would need.
    """
    for nchunk in range(NCHUNKS):
        answer = presized.update_chunk(nchunk, _chunk(nchunk))
    assert answer["written"] == NCHUNKS
    assert answer["state"] == "publishing"

    published = _published_dir() / "@personal/run.b2nd"
    for _ in range(100):  # the upload runs after the response
        if published.is_file():
            break
        time.sleep(0.05)
    assert published.is_file()

    # What landed is the array, readable as one
    np.testing.assert_array_equal(
        blosc2.open(str(published))[:], np.repeat(np.arange(NCHUNKS, dtype=DTYPE), CHUNKS[0])
    )


def test_publishing_says_where_the_array_went(presized, auth_client):
    for nchunk in range(NCHUNKS):
        presized.update_chunk(nchunk, _chunk(nchunk))
    url = f"{auth_client.urlbase}/api/publish/{presized.path}"
    response = httpx.post(url, headers={"Cookie": auth_client.cookie}, timeout=30)
    response.raise_for_status()
    assert response.json()["published"].endswith("@personal/run.b2nd")
    # ... and the array says so itself, where any reader of it can see
    vlmeta = blosc2.C2Array(presized.path, urlbase=presized.urlbase, auth_token=presized.auth_token).vlmeta
    assert vlmeta["fill_state"] == "published"
    assert vlmeta["published_url"].endswith("@personal/run.b2nd")


def test_an_unfinished_array_is_not_published(presized, auth_client):
    presized.update_chunk(0, _chunk(0))  # one of NCHUNKS
    url = f"{auth_client.urlbase}/api/publish/{presized.path}"
    response = httpx.post(url, headers={"Cookie": auth_client.cookie}, timeout=30)
    assert response.status_code == 400
    assert "unwritten" in response.text


def test_publishing_can_be_retried_after_it_was_interrupted(presized, auth_client):
    """A server that dies mid-upload leaves the array saying `publishing`.

    Nothing recovers that by itself, which is why the publish is an endpoint of
    its own and not only something the last chunk sets off.
    """
    for nchunk in range(NCHUNKS):
        presized.update_chunk(nchunk, _chunk(nchunk))
    url = f"{auth_client.urlbase}/api/publish/{presized.path}"
    assert httpx.post(url, headers={"Cookie": auth_client.cookie}, timeout=30).status_code == 200
    # Again, as a retry would: a finished array publishes as many times as asked
    assert httpx.post(url, headers={"Cookie": auth_client.cookie}, timeout=30).status_code == 200
