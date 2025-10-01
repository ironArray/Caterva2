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

Contrarily to `cat2-client adduser`, this script does not require a running Caterva2 subscriber.
Note: This script is deprecated. Use `cat2-admin adduser` instead.
"""

from caterva2 import utils
from caterva2.services import srv_utils


def main():
    # Load configuration (args)
    conf = utils.get_conf("subscriber")
    parser = utils.get_parser(
        statedir=conf.get(".statedir", "_caterva2/sub"),
    )
    parser.add_argument("username")
    parser.add_argument("password", nargs="?")
    parser.add_argument("--superuser", "-S", action="store_true", default=False)
    args = utils.run_parser(parser)

    # Add user
    statedir = args.statedir.resolve()
    user = srv_utils.add_user(args.username, args.password, args.superuser, state_dir=statedir)
    print("Password:", user.password)


if __name__ == "__main__":
    main()
