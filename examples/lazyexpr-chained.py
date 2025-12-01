###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2025 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

# Example showing how to chain lazy expressions and retrieve just a
# (compressed) slice of the result.  A temporary array is created
# and uploaded to the server, and then removed and the end.

from time import time

import blosc2
import numpy as np

import caterva2 as cat2

# Open a client to the local server
# client = cat2.Client("http://localhost:8000", ("user@example.com", "foobar11"))
# Open a client to the Cat2Cloud server
client = cat2.Client("https://cat2.cloud/demo", ("user@example.com", "foobar11"))

a = blosc2.linspace(0, 100, 100_000_000, dtype="float32", shape=(10000, 10000), urlpath="a.b2nd", mode="w")
t0 = time()
ra = client.upload(a, "@public/a.b2nd")
t1 = time() - t0
print(
    f"Time for uploading data (HTTP): {t1:.3f}s"
    f" - file size: {a.schunk.nbytes / 2**20:.2f} MB"
    f" - speed: {a.schunk.nbytes / 2**20 / t1:.2f} MB/s"
)

rla1 = client.upload(ra + 1, "@personal/la1.b2nd")
rla2 = client.upload(rla1 + 1, "@personal/la2.b2nd")

# Compute a slice of the remote array
t0 = time()
result = rla2.slice(slice(5, 9))
print(
    f"Time for computing and downloading data (compressed): {time() - t0:.3f}s"
    f" - data size: {result.schunk.nbytes / 2**10:.2f} KB"
)

np.testing.assert_allclose((a + 2)[5:9], result[:], rtol=1e-6)

# Cleanup
client.remove(ra)
