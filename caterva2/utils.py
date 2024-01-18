###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import argparse
import contextlib
import pathlib
import logging


#
# Context managers
#

@contextlib.contextmanager
def log_exception(logger, message):
    try:
        yield
    except Exception:
        logger.exception(message)


#
# Filesystem helpers
#

def walk_files(root, exclude=None):
    if exclude is None:
        exclude = set()

    for path in root.glob('**/*'):
        if path.is_file():
            relpath = path.relative_to(root)
            if str(relpath) not in exclude:
                yield path, relpath


#
# Command line helpers
#
def socket_type(string):
    host, port = string.split(':')
    port = int(port)
    return host, port


def get_parser(loglevel='warning', statedir=None,
               broker=None, http=None, id=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--loglevel', default=loglevel)
    if statedir:
        parser.add_argument('--statedir', default=statedir,
                            type=pathlib.Path)
    if broker:
        parser.add_argument('--broker', default=broker)
    if http:
        parser.add_argument('--http', default=http, type=socket_type)
    if id is not None:  # the empty string is a valid (default) ID
        parser.add_argument('--id', default=id,
                            help=("a string to distinguish services "
                                  "of the same category"))
    return parser


def run_parser(parser):
    args = parser.parse_args()

    # Logging
    loglevel = args.loglevel.upper()
    logging.basicConfig(level=loglevel)

    return args
