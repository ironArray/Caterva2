###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""
Administration commands for Caterva2 server.

This module provides commands for server administration tasks such as user management.
These commands are meant to be used on the same machine as the server.
"""

import argparse
import sys

from caterva2 import utils
from caterva2.services import srv_utils


def adduser_command(args):
    """Add a user to the server database."""
    # Load configuration
    # conf = utils.get_conf("server")

    # Add user
    statedir = args.statedir.resolve()
    user = srv_utils.add_user(args.username, args.password, args.superuser, state_dir=statedir)
    print("Password:", user.password)


def main():
    """Main entry point for cat2-admin command."""
    parser = argparse.ArgumentParser(
        prog="cat2-admin", description="Administration commands for Caterva2 server"
    )

    # Global options
    parser.add_argument(
        "--statedir",
        type=utils.get_path_type(),
        default="_caterva2/state",
        help="State directory for the server",
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # adduser subcommand
    adduser_parser = subparsers.add_parser("adduser", help="Add a user to the server database")
    adduser_parser.add_argument("username", help="Username for the new user")
    adduser_parser.add_argument("password", nargs="?", help="Password for the new user (optional)")
    adduser_parser.add_argument(
        "--superuser", "-S", action="store_true", default=False, help="Make user a superuser"
    )
    adduser_parser.set_defaults(func=adduser_command)

    # Parse arguments
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    # Execute the command
    args.func(args)


if __name__ == "__main__":
    main()
