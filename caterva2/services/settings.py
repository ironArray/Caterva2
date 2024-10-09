"""
Configuration for the subscriber only.

TODO Move toml config here.
"""

import re

from caterva2 import utils


def parse_size(size):
    if size is None:
        return None

    units = {
        "B": 1,
        "KB": 2**10,
        "MB": 2**20,
        "GB": 2**30,
        "TB": 2**40,
        "": 1,
        "KiB": 10**3,
        "MiB": 10**6,
        "GiB": 10**9,
        "TiB": 10**12,
    }
    m = re.match(r"^([\d\.]+)\s*([a-zA-Z]{0,3})$", str(size).strip())
    number, unit = float(m.group(1)), m.group(2).upper()
    return int(number * units[unit])


conf = utils.get_conf('subscriber', allow_id=True)

urlbase = conf.get('.urlbase')
login = conf.get(".login")
register = conf.get(".register")

quota = parse_size(conf.get(".quota"))
maxusers = conf.get(".maxusers")


# Not strictly necessary but useful for documentation
statedir = None
database = None  # <Database> instance
cache = None
personal = None
shared = None
public = None
