def provider_factory(settings):
    """Entry point `caterva2.providers`. Returns None when no [[server.peer]]
    is configured, so an installed-but-unconfigured c2cache is inert."""
    peer_confs = settings.conf.get(".peer") or []
    if not peer_confs:
        return None
    from c2cache.provider import C2CacheProvider

    return C2CacheProvider(settings, peer_confs)
