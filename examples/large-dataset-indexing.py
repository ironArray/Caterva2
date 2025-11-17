###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2025 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import math
import time

import blosc2

import caterva2 as cat2

urlbase = "https://cat2.cloud/demo"
root = "@public"
dataset = "examples/cube-1k-1k-1k.b2nd"

# Blosc2 approach: simple, but allows slicing of remote datasets
start = time.time()
urlpath = blosc2.URLPath(f"{root}/{dataset}", urlbase)
blosc2_ds = blosc2.open(urlpath, mode="r")
print(f"Dataset size: {math.prod(blosc2_ds.shape) * blosc2_ds.dtype.itemsize / 2**20:.3f} MB")
print(f"Blosc2 - Open dataset: {(time.time() - start) * 1000:.0f} ms")

print(type(blosc2_ds), blosc2_ds.shape, blosc2_ds.dtype)
start = time.time()
print(blosc2_ds[500:502, 302, 900:905])
print(f"Blosc2 - Slice dataset: {(time.time() - start) * 1000:.0f} ms")

# Caterva2 approach: more flexible, allowing remote management in the server
# Use Client as context manager for proper HTTP/2 connection cleanup
with cat2.Client(urlbase) as client:
    # Warmup: establish HTTP/2 connection
    _ = client.get(root)

    # Now measure with warm connection
    start = time.time()
    myroot = client.get(root)
    cat2_ds = myroot[dataset]
    print(f"Caterva2 - Get dataset handle: {(time.time() - start) * 1000:.0f} ms")

    print(type(cat2_ds), cat2_ds.shape, cat2_ds.dtype)
    start = time.time()
    print(cat2_ds[500:502, 302, 900:905])
    print(f"Caterva2 - Slice dataset: {(time.time() - start) * 1000:.0f} ms")
