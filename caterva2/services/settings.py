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

llm_enabled = conf.get(".llm.enabled", False)
llm_provider = conf.get(".llm.provider", "groq")
llm_model = conf.get(".llm.model", "openai/gpt-oss-120b")
llm_api_key_envvar = conf.get(".llm.api_key_envvar", "GROQ_API_KEY")
llm_max_iterations = conf.get(".llm.max_iterations", 10)
llm_max_history_messages = conf.get(".llm.max_history_messages", 20)
llm_max_total_tokens = conf.get(".llm.max_total_tokens", 50000)
llm_request_timeout = conf.get(".llm.request_timeout", 30)
llm_session_ttl_seconds = conf.get(".llm.session_ttl_seconds", 1800)
llm_allow_public_access = conf.get(".llm.allow_public_access", not login)
llm_max_concurrent_sessions = conf.get(".llm.max_concurrent_sessions", 20)
llm_max_input_chars = conf.get(".llm.max_input_chars", 5000)


# Not strictly necessary but useful for documentation
statedir = None
database = None  # <Database> instance
personal = None
shared = None
public = None
