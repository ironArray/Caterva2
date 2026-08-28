"""
Configuration for the server only.

TODO Move toml config here.
"""

import re

from caterva2 import utils


def parse_size(size):
    """A size written as `"500"`, `"2K"`, `"1.5G"` -- in bytes.

    Raises `ValueError` for anything else, which is what a caller reading a
    size out of a configuration file catches: `"big"` matches no spelling and
    `"1T"` no unit, and both used to come out of here as an `AttributeError` or
    a `KeyError` about this function's insides rather than about the value.
    """
    if size is None:
        return None

    units = {
        "": 1,
        "K": 2**10,
        "M": 2**20,
        "G": 2**30,
        "T": 2**40,
    }
    m = re.match(r"^([\d\.]+)\s*([a-zA-Z]{0,3})$", str(size).strip())
    if m is None:
        raise ValueError(f"{size!r} is not a size: write it as 500, 2K, 10M or 1.5G")
    number, unit = float(m.group(1)), m.group(2).upper()
    if unit not in units:
        raise ValueError(f"{size!r} names no unit this reads: {', '.join(u for u in units if u)}")
    return int(number * units[unit])


conf = utils.get_server_conf()  # FIXME This does not consider the --conf option

urlbase = conf.get(".urlbase", "http://localhost:8000")
login = conf.get(".login", True)
register = conf.get(".register", False)
demo = conf.get(".demo", False)

quota = parse_size(conf.get(".quota"))
maxusers = conf.get(".maxusers")

# Where a finished array is published, as an fsspec URL of a *directory*
# ("s3://bucket/prefix", "file:///srv/published").  A dataset names the key it
# wants underneath this and nothing above it: a destination that came from the
# client would let a caller aim the server at a bucket of their own and have it
# write user data there.  Unset means publishing is refused, which is the default.
publish_root = conf.get(".publish_root")


# Not strictly necessary but useful for documentation
statedir = None
database = None  # <Database> instance
personal = None
shared = None
public = None
peer_id = None  # set at startup from <statedir>/peer_id
