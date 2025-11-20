###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
from time import time

import blosc2

# Small example of how to query a dataset on a server
import httpx
import numpy as np

import caterva2 as cat2

# Use the demo server
URLBASE = "https://cat2.cloud/demo"
ROOT_NAME = "@public"

user_auth = None
# Uncomment the following line and use your username and password
# if the server requires authentication.
# user_auth = {'username': 'user@example.com', 'password': 'foobar11'}

client = cat2.Client(URLBASE, auth=user_auth)
# Get the list of available roots
roots = client.get_roots()
print(roots[ROOT_NAME])
# Get a handle to the root
example = client.get(ROOT_NAME)
# List the datasets in that root
print(example.file_list)
# Get a specific dataset
array = example["examples/dir1/ds-2d.b2nd"]
print(array.name, array.path)
# Get some data out of the dataset
print(array[:2])

# There are different ways to get the data
# 1. Direct download
t0 = time()
urlpath = array.get_download_url()
data = httpx.get(urlpath, auth=user_auth)
t = time() - t0
# print(urlpath)
print(f"Time for downloading data (HTTP): {t:.3f}s - {len(data.content) / 2**10:.2f} KB")
mem_array = blosc2.ndarray_from_cframe(data.content)
print(mem_array.info)

# 2. Fetch the data from the server
t0 = time()
mem_array2 = array[:]
t = time() - t0
print(f"Time for downloading data (API) : {t:.3f}s - {mem_array2.nbytes / 2**10:.2f} KB")

# Check that both methods return the same data
np.testing.assert_array_equal(mem_array2, mem_array[:])
