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

from caterva2.services import srv_utils


def sub_auth_enabled():
    return bool(os.environ.get("CATERVA2_SECRET"))


def make_sub_user(services):
    if not sub_auth_enabled():
        return None

    state_dir = services.state_dir / "subscriber"
    return srv_utils.add_user(
        "user@example.com", password="foobar11", is_superuser=True, state_dir=state_dir
    )


@pytest.fixture(scope="session")
def sub_user(services):
    # TODO: This does not work with external services,
    # as their state directory is unknown.
    # One would need to register a new user via the API there.
    return make_sub_user(services)


@pytest.fixture(scope="session")
def sub_jwt_cookie(sub_user, services):
    if not sub_user:
        return None

    username, password = sub_user
    urlbase = services.get_urlbase("subscriber")

    resp = httpx.post(f"{urlbase}/auth/jwt/login", data={"username": username, "password": password})
    resp.raise_for_status()
    return "=".join(list(resp.cookies.items())[0])
