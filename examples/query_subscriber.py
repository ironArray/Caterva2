###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

# Small example of how to query a dataset on a subscriber

import httpx
import blosc2
import caterva2 as cat2


# Use the demo server
SUB_HOST = 'demo-api.caterva2.net:8202'
ROOT_NAME = 'example'

# Get the list of available roots
roots = cat2.get_roots(f"{SUB_HOST}")
print(roots[ROOT_NAME])
# Subscribe to a root
response = cat2.subscribe(ROOT_NAME, f"{SUB_HOST}")
# Get a handle to the root
example = cat2.Root(ROOT_NAME, host=SUB_HOST)
# List the datasets in that root
print(example.node_list)
# Get a specific dataset
array = example['dir1/ds-2d.b2nd']
print(array.name, array.path)
# Get some data out of the dataset
print(array[:2])

# There are different ways to get the data
# 1. Direct download
urlpath = array.get_download_url()
print(urlpath)
data = httpx.get(urlpath)
#print(data.content)

# 2. Download to a local file
localpath = array.download()
data2 = open(localpath, 'rb').read()
#print(data2)

# Check that both methods return the same data
assert data.content == data2

# Get some info on local (downloaded) data
local_array = blosc2.open(localpath)
print(local_array.info)
