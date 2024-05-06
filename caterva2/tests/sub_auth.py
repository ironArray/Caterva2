###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import os

import httpx
import pytest


@pytest.fixture(scope='session')
def sub_jwt_cookie(services):
    from caterva2.services.subscriber import users as s_users

    if not os.environ.get(s_users.SECRET_TOKEN_ENVVAR):
        return None  # user authentication disabled

    sub_host = services.get_endpoint('subscriber')
    username, password = services.get_sub_auth()

    resp = httpx.post(f'http://{sub_host}/auth/jwt/login',
                      data=dict(username=username, password=password))
    resp.raise_for_status()
    return '='.join(list(resp.cookies.items())[0])
