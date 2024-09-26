###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################


import json
import subprocess
import sys

import pytest

from .services import TEST_CATERVA2_ROOT


@pytest.fixture
def pub_host(services):
    return services.get_endpoint(f'publisher.{TEST_CATERVA2_ROOT}')


@pytest.fixture
def sub_urlbase(services):
    return services.get_urlbase('subscriber')


def cli(cargs, binary=False, sub_user=None) -> str or dict:
    cli_path = 'caterva2.clients.cli'
    args = [sys.executable, '-m' + str(cli_path)]
    if sub_user:
        args += ['--username', sub_user.username,
                 '--password', sub_user.password]
    args += cargs
    if not binary:
        args += ['--json']
    ret = subprocess.run(args, capture_output=True, text=True)
    assert ret.returncode == 0
    out = ret.stdout
    return out if binary else json.loads(out)


def test_roots(pub_host, sub_user):
    roots = cli(['roots'], sub_user=sub_user)
    assert roots[TEST_CATERVA2_ROOT]['name'] == TEST_CATERVA2_ROOT
    assert roots[TEST_CATERVA2_ROOT]['http'] == pub_host


def test_url(sub_urlbase, sub_user):
    out = cli(['url', f'{TEST_CATERVA2_ROOT}/ds-1d.b2nd'], sub_user=sub_user)
    assert out == f'{sub_urlbase}/api/fetch/{TEST_CATERVA2_ROOT}/ds-1d.b2nd'


def test_subscribe(sub_user):
    # Subscribe once
    out = cli(['subscribe', TEST_CATERVA2_ROOT], sub_user=sub_user)
    assert out == 'Ok'

    # Subscribe again, should be a noop
    out = cli(['subscribe', TEST_CATERVA2_ROOT], sub_user=sub_user)
    assert out == 'Ok'

    # Show
    a = cli(['show', f'{TEST_CATERVA2_ROOT}/ds-1d.b2nd'],
            binary=True, sub_user=sub_user)
    b = cli(['show', f'{TEST_CATERVA2_ROOT}/ds-1d.b2nd'],
            binary=True, sub_user=sub_user)
    assert a == b
