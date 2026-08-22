###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
"""Byte ranges over `api/fetch`.

A stored dataset is served with a `FileResponse`, which implements RFC 7233 by
itself: a client can read the header, the chunk offsets and single blocks of the
frame instead of transferring it whole, which is what blosc2's `Proxy` over a
`C2Array` does.  Nothing else asserted that, so a refactor turning that one
`return` into a `StreamingResponse` would silently halve every such client's
performance -- hence the first test here.

Everything else is built as it is sent and cannot honour a range at all.  The
rest of the tests are that it says so, instead of answering a request for 32
bytes with the whole body and a 200.
"""

import pathlib

import blosc2
import httpx
import numpy as np
import pytest

from .services import TEST_CATERVA2_ROOT, TEST_STATE_DIR

DATASET = "ds-1d.b2nd"


@pytest.fixture(scope="module")
def public_dataset(client, examples_dir):
    """One stored dataset in the public area, which is what serves ranges."""
    dest = pathlib.Path(TEST_STATE_DIR) / "server/public" / DATASET
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes((examples_dir / DATASET).read_bytes())
    return DATASET


def _url(client, path):
    return f"{client.urlbase}/api/fetch/{TEST_CATERVA2_ROOT}/{path}"


def test_stored_dataset_serves_ranges(client, public_dataset):
    response = httpx.get(_url(client, public_dataset), headers={"Range": "bytes=0-31"})
    assert response.status_code == 206
    assert response.headers["accept-ranges"] == "bytes"
    total = int(response.headers["content-range"].split("/")[1])
    assert response.headers["content-range"] == f"bytes 0-31/{total}"
    assert len(response.content) == 32
    assert response.content[2:10] == b"b2frame\x00"  # the frame magic, as read from the file

    # ... and the whole of it is still the whole of it
    whole = httpx.get(_url(client, public_dataset))
    assert whole.status_code == 200
    assert len(whole.content) == total
    assert whole.content[:32] == response.content


def test_ranges_reach_into_the_frame(client, public_dataset):
    # Not just the first bytes: a block client reads at an offset, and gets the
    # bytes at that offset rather than a body that starts from zero
    whole = httpx.get(_url(client, public_dataset)).content
    response = httpx.get(_url(client, public_dataset), headers={"Range": "bytes=100-163"})
    assert response.status_code == 206
    assert response.content == whole[100:164]


def test_a_slice_refuses_ranges(client, public_dataset):
    # A slice is built and re-compressed per request, so there is no file to
    # seek into; before, this answered with the whole slice and a 200
    response = httpx.get(
        _url(client, public_dataset), params={"slice_": "0:10"}, headers={"Range": "bytes=0-31"}
    )
    assert response.status_code == 416
    assert response.headers["accept-ranges"] == "none"
    assert not response.content.startswith(b"\x00b2")


def test_a_lazy_expression_refuses_ranges(auth_client, public_dataset):
    # The case the discriminator on the client side is built around: this one is
    # computed per request, so there are no stored bytes to seek into at all
    if not auth_client:
        pytest.skip("authentication support needed")
    operand = auth_client.get(f"{TEST_CATERVA2_ROOT}/{public_dataset}")
    expression = auth_client.upload(operand + 0, "@personal/rangeexpr.b2nd")
    url = f"{auth_client.urlbase}/api/fetch/{expression.path}"
    headers = {"Cookie": auth_client.cookie}

    response = httpx.get(url, headers={**headers, "Range": "bytes=0-31"})
    assert response.status_code == 416
    assert response.headers["accept-ranges"] == "none"

    # ... and it still fetches whole, as it always did
    whole = httpx.get(url, headers=headers)
    assert whole.status_code == 200
    assert whole.headers["accept-ranges"] == "none"
    np.testing.assert_array_equal(blosc2.ndarray_from_cframe(whole.content)[:], operand[:])


def test_a_streamed_response_says_it_serves_no_ranges(client, public_dataset):
    # No Range header at all: the answer still carries the fact, so a client can
    # tell without asking for bytes it may not get
    response = httpx.get(_url(client, public_dataset), params={"slice_": "0:10"})
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "none"


def test_info_says_a_stored_dataset_serves_ranges(client, public_dataset):
    # Reported a request earlier than `api/fetch` would: a client deciding
    # whether to read blocks asks `api/info` anyway, and can then skip the probe
    # it would otherwise spend finding out that the answer is yes.  A field of
    # the dataset's description, not an `Accept-Ranges` header, which would be
    # claiming that `api/info` itself served ranges
    url = f"{client.urlbase}/api/info/{TEST_CATERVA2_ROOT}/{public_dataset}"
    response = httpx.get(url)
    assert response.status_code == 200
    assert response.json()["accept_ranges"] == "bytes"
    assert "accept-ranges" not in response.headers


def test_download_refuses_ranges(client, public_dataset):
    url = f"{client.urlbase}/api/download/{TEST_CATERVA2_ROOT}/{public_dataset}"
    response = httpx.get(url, headers={"Range": "bytes=0-31"})
    assert response.status_code == 416
    assert response.headers["accept-ranges"] == "none"

    whole = httpx.get(url)
    assert whole.status_code == 200
    assert whole.headers["accept-ranges"] == "none"
