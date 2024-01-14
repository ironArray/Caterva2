###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import pathlib

from caterva2 import api_utils


# Defaults
bro_host_default = 'localhost:8000'
pub_host_default = 'localhost:8001'
sub_host_default = 'localhost:8002'


def get_roots(host=sub_host_default):
    return api_utils.get(f'http://{host}/api/roots')


class Root:
    def __init__(self, name, host=sub_host_default):
        self.name = name
        self.host = host
        ret = api_utils.post(f'http://{host}/api/subscribe/{name}')
        if ret != 'Ok':
            roots = get_roots(host)
            raise ValueError(f'Could not subscribe to root {name}'
                             f' (only {roots.keys()} available)')
        self.node_list = api_utils.get(f'http://{host}/api/list/{name}')

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

    def get_download_url(self, key=None):
        slice_ = api_utils.slice_to_string(key)
        download_path = api_utils.get_download_url(
            self.host, self.path, {'slice_': slice_, 'download': True})
        return download_path

    def download(self, key=None):
        url = self.get_download_url(key)
        return api_utils.download_url(url, self.path)

class Dataset(File):
    def __init__(self, name, root, host):
        super().__init__(name, root, host)
        self.json = api_utils.get(f'http://{host}/api/info/{self.path}')

    def __repr__(self):
        return f'<Dataset: {self.path}>'

    def __getitem__(self, key):
        slice_ = api_utils.slice_to_string(key)
        data = api_utils.get_download_url(self.host, self.path, {'slice_': slice_})
        return data
