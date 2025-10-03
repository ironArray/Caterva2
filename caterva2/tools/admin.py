###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""
Admin tool for Caterva2.  Currently only supports adding users.
"""

import sys

from caterva2 import utils
from caterva2.services import srv_utils


def adduser_command(args):
    """Add a user to the server database."""
    statedir = args.statedir.resolve()
    user = srv_utils.add_user(args.username, args.password, args.superuser, state_dir=statedir)
    print(f"User '{args.username}' added successfully.")
    print("Password:", user.password)


def main():
    # Load configuration (args)
    conf = utils.get_conf("server")
    parser = utils.get_parser(
        statedir=conf.get(".statedir", "_caterva2/state"),
    )

    subparsers = parser.add_subparsers(dest="command", required=True, help="sub-command help")

    # Subparser for 'adduser'
    adduser_parser = subparsers.add_parser("adduser", help="Add a new user.")
    adduser_parser.add_argument("username", help="The username to be added.")
    adduser_parser.add_argument("password", nargs="?", help="The password for the new user.")
    adduser_parser.add_argument(
        "--superuser", "-S", action="store_true", default=False, help="Make the user a superuser."
    )
    adduser_parser.set_defaults(func=adduser_command)

    # Parse args and call the function
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
