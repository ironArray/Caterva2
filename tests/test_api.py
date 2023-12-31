###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import caterva2 as cat2
import pathlib


root_default = 'foo'


def test_roots():
    roots = cat2.get_roots()
    assert roots[root_default]['name'] == root_default
    assert roots[root_default]['http'] == cat2.pub_host_default

def test_root():
    myroot = cat2.Root(root_default, host=cat2.sub_host_default)
    assert myroot.name == root_default
    assert myroot.host == cat2.sub_host_default

def test_list():
    myroot = cat2.Root(root_default, host=cat2.sub_host_default)
    example = pathlib.Path(__file__).parent.parent / 'root-example'
    nodes = set(str(f.relative_to(str(example))) for f in example.rglob("*") if f.is_file())
    assert set(myroot.node_list) == nodes

def test_file():
    myroot = cat2.Root(root_default, host=cat2.sub_host_default)
    file = myroot['foo/README.md']
    assert file.name == 'foo/README.md'
    assert file.host == cat2.sub_host_default
