###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import json
import os
import re
import urllib.parse

# Requirements
import blosc2
import numpy as np

MAX_QUERY_CHARS = 7_500
"""How long an *encoded* query may be before the parameters go in a body instead.

Measured after percent-encoding, which is what actually travels: a list of
coordinates is mostly `[`, `]` and `,`, and each of those is three characters on
the wire, so the raw string is no guide to whether the request line fits.

The bound is what deployments accept rather than what a client can build.  A
request line has to pass every hop: uvicorn's h11 caps one at 16 KiB, nginx's
`large_client_header_buffers` at 8 KiB by default, and some proxies lower still.
This leaves room under the smallest of those for the path and the rest of the
line.  `api/fetch` answers a POST carrying the same parameters for exactly this
reason, so a key of more coordinates than a URL holds is a change of verb and
nothing else.  Below it nothing changes, which is what keeps every server that
ever served a GET serving one.
"""


def query_too_long(params):
    """Whether *params* spell a query string too long to send in an URL.

    The encoded length, not the raw one: see `MAX_QUERY_CHARS`.
    """
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return len(query) > MAX_QUERY_CHARS


def expand_ellipsis(key, ndim=None):
    """*key* with its `Ellipsis` written out as the full slices it stands for.

    `Ellipsis` is a shorthand for "every dimension not named here", so what it
    stands for depends on how many there are.  Where *ndim* is known it is
    written out; where it is not, a trailing one is dropped -- the dimensions it
    covers are taken whole either way, which is what leaving them unnamed already
    says -- and one anywhere else is refused rather than read as a different key,
    since dropping it would shift every entry after it onto the wrong dimension.
    """
    entries = key if isinstance(key, tuple) else (key,)
    count = sum(1 for entry in entries if entry is Ellipsis)
    if count == 0:
        return key
    if count > 1:
        raise IndexError("A key may hold at most one Ellipsis")
    at = next(i for i, entry in enumerate(entries) if entry is Ellipsis)
    named = len(entries) - 1
    if ndim is None:
        if at != named:  # not trailing, so what it covers cannot be counted here
            raise IndexError(
                "Cannot expand an Ellipsis that is not the last entry of the key "
                "without knowing how many dimensions the dataset has"
            )
        filled = ()
    else:
        if named > ndim:
            raise IndexError(f"The key names {named} dimensions where the dataset has {ndim}")
        filled = (slice(None),) * (ndim - named)
    return entries[:at] + filled + entries[at + 1 :]


def slice_to_string(slice_, ndim=None):
    slice_ = expand_ellipsis(slice_, ndim)
    if slice_ is None or slice_ == () or slice_ == slice(None):
        return ""
    slice_parts = []
    if not isinstance(slice_, tuple):
        slice_ = (slice_,)
    for index in slice_:
        if isinstance(index, int):
            slice_parts.append(str(index))
        elif isinstance(index, slice):
            # Written out rather than tested for truth: a bound of 0 is a bound,
            # and an empty string here says "no bound at all", which is the whole
            # dimension.  `0:0` selects nothing and has to keep saying so
            start = "" if index.start is None else str(index.start)
            stop = "" if index.stop is None else str(index.stop)
            if index.step not in (1, None):
                raise IndexError("Only step=1 is supported")
            # step = index.step or ''
            slice_parts.append(f"{start}:{stop}")
        else:
            # Anything else has no spelling here, and dropping it would widen the
            # request rather than narrow it: a fancy index skipped this way asks
            # `api/fetch` for the whole dataset and hands back all of it, which is
            # neither what was asked for nor a smaller answer.  Coordinates go
            # over as `indices` instead; see `key_to_indices`
            raise IndexError(
                f"Cannot ask a Caterva2 server for {index!r}: only integers and "
                "step-1 slices can be expressed in a slice request"
            )
    return ", ".join(slice_parts)


def key_to_indices(key, ndim=None):
    """*key* as the `indices` parameter names it, or None if it needs no such thing.

    `api/fetch` takes a fancy key as JSON, one entry per dimension, because a
    list of coordinates has no unambiguous reading as the comma-separated string
    a slice is.  None where the key is a plain box, which `slice_` says more
    cheaply and which every server understands.

    The server gathers the points and sends those: reading them here would mean
    fetching the blocks they live in and picking single values out, and a block
    is nearly all waste for one coordinate.

    The key goes over as it was written, not as numpy would expand it, since the
    server indexes a real array with it and its reading is numpy's own.
    """
    key = expand_ellipsis(key, ndim)
    entries = key if isinstance(key, tuple) else (key,)
    if not any(isinstance(k, (list, np.ndarray)) for k in entries):
        return None
    out = []
    for entry in entries:
        if isinstance(entry, (list, np.ndarray)):
            coords = np.asarray(entry)
            if coords.dtype == np.bool_:
                # A mask is the coordinates it selects, said in as many bytes as
                # the array is long; the coordinates themselves are what travels
                coords = np.flatnonzero(coords)
            if not np.issubdtype(coords.dtype, np.integer):
                raise IndexError(f"Cannot index a remote array with {entry!r}")
            out.append([int(v) for v in coords.reshape(-1)])
        elif isinstance(entry, (int, np.integer)):
            out.append(int(entry))
        elif isinstance(entry, slice):
            if entry.step not in (1, None):
                raise IndexError("Only step=1 is supported")
            # As `slice_to_string` spells one, bound of 0 included: an empty
            # string is "no bound", which is the whole dimension and not `0:0`
            start = "" if entry.start is None else str(entry.start)
            stop = "" if entry.stop is None else str(entry.stop)
            out.append(None if entry == slice(None) else f"{start}:{stop}")
        else:
            raise IndexError(f"Cannot index a remote array with {entry!r}")
    return json.dumps(out, separators=(",", ":"))


def get_download_url(path, urlbase):
    return f"{urlbase}/api/download/{path}"


def get_handle_url(path, urlbase):
    # Get the root in path (first element in path)
    # root = path.split("/")[0]
    # return f"{urlbase}/roots/{path}?roots={root}"
    # We don't want to show other datasets in the same root
    return f"{urlbase}/roots/{path}"


def b2_unpack(filepath):
    schunk = blosc2.open(filepath)
    outfile = filepath.with_suffix("")
    with open(outfile, "wb") as f:
        for i in range(schunk.nchunks):
            data = schunk.decompress_chunk(i)
            f.write(data)
    os.unlink(filepath)
    return outfile


# Not completely RFC6266-compliant, but probably good enough.
_attachment_b2fname_rx = re.compile(r';\s*filename\*?\s*=\s*"([^"]+\.b2)"')
