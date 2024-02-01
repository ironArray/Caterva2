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

import pytest

from .services import TEST_PUBLISHED_ROOT


@pytest.fixture
def pub_host(configuration):
    return configuration.get('publisher.http', cat2.pub_host_default)


def cli(args, binary=False) -> str or dict:
    cli_path = 'caterva2.clients.cli'
    args = [sys.executable, '-m' + str(cli_path)] + args
    if not binary:
        args += ['--json']
    ret = subprocess.run(args, capture_output=True, text=True)
    assert ret.returncode == 0
    out = ret.stdout
    return out if binary else json.loads(out)


def test_roots(services, pub_host):
    roots = cli(['roots'])
    assert roots[TEST_PUBLISHED_ROOT]['name'] == TEST_PUBLISHED_ROOT
    assert roots[TEST_PUBLISHED_ROOT]['http'] == pub_host


def test_url(services, pub_host):
    out = cli(['url', TEST_PUBLISHED_ROOT])
    assert out == [f'http://{pub_host}']


def test_subscribe(services):
    # Subscribe once
    out = cli(['subscribe', TEST_PUBLISHED_ROOT])
    assert out == 'Ok'

    # Subscribe again, should be a noop
    out = cli(['subscribe', TEST_PUBLISHED_ROOT])
    assert out == 'Ok'

    # Show
    a = cli(['show', f'{TEST_PUBLISHED_ROOT}/ds-1d.b2nd'], binary=True)
    b = cli(['show', f'{TEST_PUBLISHED_ROOT}/ds-1d.b2nd'], binary=True)
    assert a == b
