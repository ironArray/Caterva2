def provider_factory(settings):
    """Entry point `caterva2.providers`. Returns None when no [[server.peer]]
    is configured, so the bundled-but-unconfigured C2Cache provider is inert."""
    peer_confs = settings.conf.get(".peer") or []
    if not peer_confs:
        return None
    from .provider import C2CacheProvider

    return C2CacheProvider(settings, peer_confs)
