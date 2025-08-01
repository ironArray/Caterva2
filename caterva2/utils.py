###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import argparse
import datetime
import logging
import pathlib
import tomllib as toml

#
# Datetime related
#


def epoch_to_iso(time):
    return datetime.datetime.fromtimestamp(time, tz=datetime.UTC).isoformat()


#
# Filesystem helpers
#


def walk_files(root, exclude=None):
    if exclude is None:
        exclude = set()

    if root is not None:
        for path in root.glob("**/*"):
            if path.is_file():
                relpath = path.relative_to(root)
                if str(relpath) not in exclude:
                    yield path, relpath


def iterdir(root):
    for path in root.iterdir():
        relpath = path.relative_to(root)
        yield path, relpath


#
# Command line helpers
#
def urlbase_type(string):
    if not (string.startswith("http://") or string.startswith("https://")):
        raise ValueError("invalid HTTP(S) URL base", string)
    return string


class Socket(str):
    host = None
    port = None
    uds = None

    def __init__(self, string):
        try:
            host, port = string.rsplit(":", maxsplit=1)
        except ValueError:
            host = port = None

        if host and port:
            # Host and port
            self.host = host
            self.port = int(port)
            # Remove brackets from IPv6 addresses
            if host.startswith("[") and host.endswith("]"):
                self.host = host[1:-1]
        else:
            # Unix domain socket
            self.uds = string


def get_parser(loglevel="warning", statedir=None, http=None):
    parser = argparse.ArgumentParser()
    _add_preliminary_args(parser)  # just for help purposes
    if http is not None:
        parser.add_argument(
            "--http", default=http, type=Socket, help="Listen to given hostname:port or unix socket"
        )
    if statedir is not None:
        parser.add_argument("--statedir", default=statedir, type=pathlib.Path)
    parser.add_argument("--loglevel", default=loglevel)
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


class Conf:
    def __init__(self, conf, prefix=None):
        self._conf = conf
        self.prefix = prefix
        self._pfx = prefix if prefix else None

    def get(self, key, default=None):
        """Get configuration item with dot-separated `key`.

        The `default` value is returned if any part of the `key` is missing.

        The prefix (if given) is prepended to a `key` that starts with a dot.
        If an ID was given, it is appended to `prefix`, separated by a dot.
        """
        if self._pfx is not None and key.startswith("."):
            key = self._pfx + key
        keys = key.split(".")
        conf = self._conf
        for k in keys:
            conf = conf.get(k)
            if conf is None:
                return default
        return conf


def _add_preliminary_args(parser):
    parser.add_argument(
        "--conf",
        default="caterva2.toml",
        type=pathlib.Path,
        help=("path to alternative configuration file " "(may not exist)"),
    )


def get_conf(prefix=None):
    """Get settings from the configuration file, if existing.

    If the configuration file does not exist, return an empty configuration.

    You may get the value for a key from the returned configuration ``conf``
    with ``conf.get('path.to.item'[, default])``.  If a `prefix` is given and
    the key starts with a dot, like ``.path.to.item``, `prefix` is prepended
    to it.
    """
    parser = argparse.ArgumentParser(add_help=False)
    _add_preliminary_args(parser)
    opts = parser.parse_known_args()[0]

    try:
        with open(opts.conf, "rb") as conf_file:
            conf = toml.load(conf_file)
            return Conf(conf, prefix=prefix)
    except FileNotFoundError:
        return Conf({}, prefix=prefix)
