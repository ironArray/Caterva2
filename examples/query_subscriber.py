###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import os
from time import time

# Small example of how to query a dataset on a subscriber

import httpx
import blosc2
import numpy as np

import caterva2 as cat2


# Use the demo server
URLBASE = 'https://demo.caterva2.net/'
ROOT_NAME = 'example'

user_auth = None
# Uncomment the following line and use your username and password
# if the subscriber requires authentication.
#user_auth = {'username': 'user@example.com', 'password': 'foobar11'}

auth_cookie = (cat2.api_utils.get_auth_cookie(URLBASE, user_auth)
               if user_auth else None)

# Get the list of available roots
roots = cat2.get_roots(URLBASE, auth_cookie=auth_cookie)
print(roots[ROOT_NAME])
# Subscribe to a root
response = cat2.subscribe(ROOT_NAME, URLBASE, auth_cookie=auth_cookie)
# Get a handle to the root
example = cat2.Root(ROOT_NAME, urlbase=URLBASE, user_auth=user_auth)
# List the datasets in that root
print(example.file_list)
# Get a specific dataset
array = example['dir1/ds-2d.b2nd']
print(array.name, array.path)
# Get some data out of the dataset
print(array[:2])

# There are different ways to get the data
# 1. Direct download
t0 = time()
urlpath = array.get_download_url()
data = httpx.get(urlpath, headers=({'Cookie': auth_cookie} if auth_cookie else None))
t = time() - t0
# print(urlpath)
print(f"Time for downloading data (HTTP): {t:.3f}s - {len(data.content) / 2**10:.2f} KB")
mem_array = blosc2.ndarray_from_cframe(data.content)
# print(mem_array.info)

# 2. Download to a local file
t0 = time()
localpath = array.download()
t = time() - t0
print(f"Time for downloading data (API) : {t:.3f}s - {os.path.getsize(localpath) / 2**10:.2f} KB")
data2 = open(localpath, 'rb').read()
disk_array = blosc2.open(localpath)

# Check that both methods return the same data
np.testing.assert_array_equal(disk_array[:], mem_array[:])

# Get some info on local (downloaded) data
print(disk_array.info)
