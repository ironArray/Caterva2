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

# Requirements
import blosc2
import numpy as np

MAX_QUERY_CHARS = 60_000
"""How long a query may be before the parameters go in a body instead.

A URL is not a body: past roughly this much the HTTP client gives up, with an
error about URL components rather than about coordinates.  `api/fetch` answers a
POST carrying the same parameters for exactly this reason, so a key of more
coordinates than a URL holds is a change of verb and nothing else.  Below it
nothing changes, which is what keeps every server that ever served a GET
serving one.
"""


def slice_to_string(slice_):
    if slice_ is None or slice_ == () or slice_ == slice(None):
        return ""
    slice_parts = []
    if not isinstance(slice_, tuple):
        slice_ = (slice_,)
    for index in slice_:
        if isinstance(index, int):
            slice_parts.append(str(index))
        elif isinstance(index, slice):
            start = index.start or ""
            stop = index.stop or ""
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


def key_to_indices(key):
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
            out.append(None if entry == slice(None) else f"{entry.start or ''}:{entry.stop or ''}")
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
