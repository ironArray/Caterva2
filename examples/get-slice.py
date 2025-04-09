###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2025 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import math
from time import time

import caterva2 as cat2

# Choose the dataset to open
# dsname = 'examples/sa-1M.b2nd'
dsname = "examples/lung-jpeg2000_10x.b2nd"
# dsname = 'examples/uncompressed_lung-jpeg2000_10x.b2nd'

# Open a client to the Cat2Cloud server
client = cat2.Client("https://cat2.cloud/demo")
info = client.get_info(f"@public/{dsname}")
print(f"Client info: {info.keys()}")
typesize = info["schunk"]["cparams"]["typesize"]
# Open the public root
root = client.get("@public")
# Open the remote array
t0 = time()
remote_array = root[dsname]
size = math.prod(remote_array.shape) * typesize
print(f"Time for opening data (HTTP): {time() - t0:.3f}s - file size: {size / 2**10:.2f} KB")

# Download a slice of the remote array as a numpy array
t0 = time()
na = remote_array[5:9]
print(f"Time for reading data (getitem): {time() - t0:.3f}s - data size: {na.nbytes / 2**10:.2f} KB")

# Download a slice of the remote array
t0 = time()
a = remote_array.slice(slice(5, 9))
print(f"Time for reading data (slice): {time() - t0:.3f}s - data size: {a.schunk.nbytes / 2**10:.2f} KB")
