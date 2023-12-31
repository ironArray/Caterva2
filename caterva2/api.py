###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

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

    def __repr__(self):
        return f'<File: {self.root}/{self.name}>'


class Dataset(File):
    def __init__(self, name, root, host):
        super().__init__(name, root, host)
        self.json = utils.get(f'http://{host}/api/info/{root}/{name}')

    def __repr__(self):
        return f'<Dataset: {self.root}/{self.name}>'
