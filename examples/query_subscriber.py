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


SUB_HOST = 'demo.caterva2.net:8002'  # why automatic redirection does not work here?
ROOT_NAME = 'example'

roots = cat2.get_roots(f"{SUB_HOST}")
print(roots[ROOT_NAME])
response = cat2.subscribe(ROOT_NAME, f"{SUB_HOST}")
print(response)
example = cat2.Root(ROOT_NAME, host=SUB_HOST)
print(example.node_list)
array = example['dir1/ds-2d.b2nd']
print(array.name, array.path)
print(array[:2])

# The following code is not working yet
# urlpath = array.get_download_url()
# data = httpx.get(urlpath)
# print(data.content)
