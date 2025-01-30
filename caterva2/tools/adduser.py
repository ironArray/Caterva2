###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""
Add a user to the subscriber database.

Contrarily to `cat2cli adduser`, this script does not require a running Caterva2 subscriber.
"""

from caterva2 import utils
from caterva2.services import srv_utils


def main():
    conf = utils.get_conf("subscriber", allow_id=True)
    _stdir = "_caterva2/sub" + (f".{conf.id}" if conf.id else "")
    parser = utils.get_parser(statedir=conf.get(".statedir", _stdir), id=conf.id)
    parser.add_argument("username")
    parser.add_argument("password", nargs="?")
    parser.add_argument("--superuser", "-S", action="store_true", default=False)
    args = utils.run_parser(parser)

    statedir = args.statedir.resolve()
    user = srv_utils.add_user(args.username, args.password, args.superuser, state_dir=statedir)
    print("Password:", user.password)


if __name__ == "__main__":
    main()
