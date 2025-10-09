"""
Configuration for the server only.

TODO Move toml config here.
"""

import re

from caterva2 import utils


def parse_size(size):
    if size is None:
        return None

    units = {
        "": 1,
        "K": 2**10,
        "k": 2**10,
        "M": 2**20,
        "m": 2**20,
        "G": 2**30,
        "g": 2**30,
    }
    m = re.match(r"^([\d\.]+)\s*([a-zA-Z]{0,3})$", str(size).strip())
    number, unit = float(m.group(1)), m.group(2).upper()
    return int(number * units[unit])


conf = utils.get_server_conf()  # FIXME This does not consider the --conf option

urlbase = conf.get(".urlbase", "http://localhost:8000")
login = conf.get(".login", True)
register = conf.get(".register", False)
demo = conf.get(".demo", False)

quota = parse_size(conf.get(".quota"))
maxusers = conf.get(".maxusers")


# Not strictly necessary but useful for documentation
statedir = None
database = None  # <Database> instance
personal = None
shared = None
public = None
