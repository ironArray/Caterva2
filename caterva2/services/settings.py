"""
Configuration for the subscriber only.

TODO Move toml config here.
"""

from caterva2 import utils


conf = utils.get_conf('subscriber', allow_id=True)
urlbase = conf.get('.urlbase')
