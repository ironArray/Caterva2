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
import os
import pathlib
import sys
import logging

try:
    import tomllib as toml
except ImportError:
    import tomli as toml


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


def get_parser(loglevel='warning', statedir=None, id=None,
               http=None, broker=None):
    parser = argparse.ArgumentParser()
    _add_preliminary_args(parser, id=id)  # just for help purposes
    if broker:
        parser.add_argument('--broker', default=broker)
    if http:
        parser.add_argument('--http', default=http, type=socket_type)
    if statedir:
        parser.add_argument('--statedir', default=statedir,
                            type=pathlib.Path)
    parser.add_argument('--loglevel', default=loglevel)
    return parser


def run_parser(parser):
    args = parser.parse_args()

    # Logging
    loglevel = args.loglevel.upper()
    logging.basicConfig(level=loglevel)

    return args


#
# Configuration file
#

conf_file_name = 'caterva2.toml'


class Conf:
    def __init__(self, conf, prefix=None, id=None):
        self._conf = conf
        self.prefix = prefix
        self.id = id

        self._pfx = ((f"{prefix}.{id}" if id else prefix) if prefix
                     else None)

    def get(self, key, default=None):
        """Get configuration item with dot-separated `key`.

        The `default` value is returned if any part of the `key` is missing.

        The prefix (if given) is prepended to a `key` that starts with a dot.
        If an ID was given, it is appended to `prefix`, separated by a dot.
        """
        if self._pfx is not None and key.startswith('.'):
            key = self._pfx + key
        keys = key.split('.')
        conf = self._conf
        for k in keys:
            conf = conf.get(k)
            if conf is None:
                return default
        return conf


def _add_preliminary_args(parser, id=None):
    parser.add_argument('--conf', default=conf_file_name,
                        type=pathlib.Path,
                        help=("path to alternative configuration file "
                              "(may not exist)"))
    if id is not None:  # the empty string is a valid (default) ID
        parser.add_argument('--id', default=id,
                            help=("a string to distinguish services "
                                  "of the same category"))


def _parse_preliminary_args(allow_id):
    parser = argparse.ArgumentParser(add_help=False)
    _add_preliminary_args(parser, id='' if allow_id else None)
    return parser.parse_known_args()[0]


def get_conf(prefix=None, allow_id=False):
    """Get settings from the configuration file, if existing.

    If the configuration file does not exist, return an empty configuration.

    You may get the value for a key from the returned configuration ``conf``
    with ``conf.get('path.to.item'[, default])``.  If a `prefix` is given and
    the key starts with a dot, like ``.path.to.item``, `prefix` is prepended
    to it.  If `allow_id` is true and command line arguments has a non-empty
    value for the ``--id`` option, the value gets appended to `prefix`,
    separated by a dot.

    For instance, with ``conf = get_conf('foo')`` and ``--id=bar``,
    ``conf.get('.item')`` is equivalent to ``conf.get('foo.bar.item')``.
    """
    opts = _parse_preliminary_args(allow_id)
    if allow_id and opts.id:
        if any(p in opts.id for p in [os.curdir, os.pardir, os.sep]):
            raise ValueError("invalid identifier", opts.id)
    id_ = opts.id if allow_id else None
    try:
        with open(opts.conf, 'rb') as conf_file:
            conf = toml.load(conf_file)
            return Conf(conf, prefix=prefix, id=id_)
    except FileNotFoundError:
        return Conf({}, prefix=prefix, id=id_)
