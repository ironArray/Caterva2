###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################


import json
import os
import subprocess
import sys

from .services import TEST_CATERVA2_ROOT


def cli(cargs, binary=False, sub_user=None) -> str or dict:
    cli_path = "caterva2.clients.cli"
    args = [sys.executable, "-m" + str(cli_path)]
    # Always use the caterva2.toml in the tests directory
    parent = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(parent, "caterva2.toml")
    args += ["--conf", config_file, "--server", "pytest"]
    if sub_user:
        args += ["--username", sub_user.username, "--password", sub_user.password]
    args += cargs
    if not binary:
        args += ["--json"]
    ret = subprocess.run(args, capture_output=True, text=True)
    assert ret.returncode == 0
    out = ret.stdout
    return out if binary else json.loads(out)


def test_roots(sub_user):
    roots = cli(["roots"], sub_user=sub_user)
    assert roots[TEST_CATERVA2_ROOT]["name"] == TEST_CATERVA2_ROOT


def test_url(services, sub_user):
    urlbase = services.get_urlbase()
    out = cli(["url", f"{TEST_CATERVA2_ROOT}/ds-1d.b2nd"], sub_user=sub_user)
    assert out == f"{urlbase}/api/download/{TEST_CATERVA2_ROOT}/ds-1d.b2nd"
