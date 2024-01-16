###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################


import caterva2 as cat2
import json
import subprocess
import sys

from .services import TEST_PUBLISHED_ROOT as root_default


def cli(args, binary=False):
    cli_path = 'caterva2.clients.cli'
    args = [sys.executable, '-m' + str(cli_path)] + args
    if not binary:
        args += ['--json']
    ret = subprocess.run(args, capture_output=True, text=True)
    assert ret.returncode == 0
    out = ret.stdout
    return out if binary else json.loads(out)


def test_roots(services):
    roots = cli(['roots'])
    assert roots[root_default]['name'] == root_default
    assert roots[root_default]['http'] == cat2.pub_host_default


def test_url(services):
    out = cli(['url', root_default])
    assert out == ['http://localhost:8001']


def test_subscribe(services):
    # Subscribe once
    out = cli(['subscribe', root_default])
    assert out == 'Ok'

    # Subscribe again, should be a noop
    out = cli(['subscribe', root_default])
    assert out == 'Ok'

    # Show
    a = cli(['show', f'{root_default}/ds-1d.b2nd'], binary=True)
    b = cli(['show', f'{root_default}/ds-1d.b2nd'], binary=True)
    assert a == b
