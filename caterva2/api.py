###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import pathlib

from caterva2 import utils


# Defaults
bro_host_default = 'localhost:8000'
pub_host_default = 'localhost:8001'
sub_host_default = 'localhost:8002'


def get_roots(host=sub_host_default):
    return utils.get(f'http://{host}/api/roots')

class Root:
    def __init__(self, name, host=sub_host_default):
        self.name = name
        self.host = host
        ret = utils.post(f'http://{host}/api/subscribe/{name}')
        if ret != 'Ok':
            roots = get_roots(host)
            raise ValueError(f'Could not subscribe to root {name}'
                             f' (only {roots.keys()} available)')
        self.node_list = utils.get(f'http://{host}/api/list/{name}')

    def __repr__(self):
        return f'<Root: {self.name}>'

    def __getitem__(self, node):
        if node.endswith((".b2nd", ".b2frame")):
            return Dataset(node, root=self.name, host=self.host)
        else:
            return File(node, root=self.name, host=self.host)


class File:
    def __init__(self, name, root, host):
        self.root = root
        self.name = name
        self.host = host
        self.path = pathlib.Path(f'{self.root}/{self.name}')

    def __repr__(self):
        return f'<File: {self.path}>'


def slice_to_string(indexes):
    slice_parts = []
    if not isinstance(indexes, tuple):
        indexes = (indexes,)
    for index in indexes:
        if isinstance(index, int):
            slice_parts.append(str(index))
        elif isinstance(index, slice):
            start = index.start or ''
            stop = index.stop or ''
            if index.step not in (1, None):
                raise IndexError('Only step=1 is supported')
            step = index.step or ''
            slice_parts.append(f"{start}:{stop}:{step}")
    return ", ".join(slice_parts)

class Dataset(File):
    def __init__(self, name, root, host):
        super().__init__(name, root, host)
        self.json = utils.get(f'http://{host}/api/info/{root}/{name}')

    def __repr__(self):
        return f'<Dataset: {self.path}>'

    def __getitem__(self, indexes):
        slice = slice_to_string(indexes)
        array, schunk = utils.download(self.host, self.path, {'slice': slice})
        if array:
            data = array[:] if array.ndim > 0 else array[()]
        else:
            data = schunk[:]  # byte string
        return data
