###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import argparse
import logging
import pathlib
import sys
import tomllib as toml


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


def _get_parser(filename, description=None):
    parser = argparse.ArgumentParser(description=description)
    _add_conf_argument(filename, parser)  # just for help purposes
    parser.add_argument("--loglevel")
    return parser


def config_log(args, conf):
    loglevel = args.loglevel or conf.get(".loglevel", "warning")
    logging.basicConfig(level=loglevel.upper())


def get_client_parser(description=None):
    parser = _get_parser("caterva2.toml", description=description)
    parser.add_argument("--server", default="default")
    parser.add_argument("--url", type=urlbase_type, help="Default http://localhost:8000")
    parser.add_argument("--username")
    parser.add_argument("--password")
    return parser


def get_server_parser():
    parser = _get_parser("caterva2-server.toml")
    parser.add_argument("--listen", type=Socket, help="Listen to given hostname:port or unix socket")
    parser.add_argument("--statedir", type=pathlib.Path, help="Default _caterva2/state")
    return parser


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


def _add_conf_argument(default, parser):
    parser.add_argument(
        "--conf",
        default=default,
        type=pathlib.Path,
        help=("path to alternative configuration file " "(may not exist)"),
    )


def _find_config_file(filename):
    """Find configuration file using a standard search path.

    Search order:
    1. Current directory (./filename)
    2. User home directory (~/.filename or ~/filename without extension prefix)
    3. System-wide config (/etc/filename) - Unix/Linux only

    Args:
        filename: The default config filename (e.g., 'caterva2.toml')

    Returns:
        pathlib.Path of the first found config file, or Path(filename) if none found
    """
    # First, check if --conf was explicitly provided
    parser = argparse.ArgumentParser(add_help=False)
    _add_conf_argument(filename, parser)
    opts = parser.parse_known_args()[0]

    # If --conf was explicitly provided (not default), use it as-is
    if opts.conf != pathlib.Path(filename):
        return opts.conf

    # Search path: current dir -> home dir -> /etc (Unix only)
    search_paths = [
        pathlib.Path.cwd() / filename,  # Current directory
        pathlib.Path.home() / f".{filename}",  # Home with dot prefix
    ]

    # Add /etc path on Unix-like systems (not Windows)
    if sys.platform != "win32":
        search_paths.append(pathlib.Path("/etc") / filename)

    # Return first existing file, or the default if none found
    for path in search_paths:
        if path.exists():
            return path

    # If no config file found, return the default (current dir) path
    return pathlib.Path(filename)


def _get_conf(filename, prefix=None):
    """Get settings from the configuration file, if existing.

    If the configuration file does not exist, return an empty configuration.

    You may get the value for a key from the returned configuration ``conf``
    with ``conf.get('path.to.item'[, default])``.  If a `prefix` is given and
    the key starts with a dot, like ``.path.to.item``, `prefix` is prepended
    to it.
    """
    config_path = _find_config_file(filename)

    try:
        with open(config_path, "rb") as conf_file:
            conf = toml.load(conf_file)
            return Conf(conf, prefix=prefix)
    except FileNotFoundError:
        return Conf({}, prefix=prefix)


def get_client_conf(conf="caterva2.toml", server="default"):
    return _get_conf(conf, server)


def get_server_conf(conf="caterva2-server.toml"):
    return _get_conf(conf, "server")
