# https://gist.github.com/florimondmanca/d56764d78d748eb9f73165da388e546e?permalink_comment_id=5088020#gistcomment-5088020

import typing

import httpx
import urllib3
from httpx._config import create_ssl_context
from httpx._types import CertTypes, ProxyTypes, SyncByteStream, VerifyTypes


def httpx_headers_to_urllib3_headers(headers: httpx.Headers) -> urllib3.HTTPHeaderDict:
    urllib3_headers = urllib3.HTTPHeaderDict()
    for name, value in headers.multi_items():
        urllib3_headers.add(name, value)
    return urllib3_headers


class ResponseStream(SyncByteStream):
    CHUNK_SIZE = 1024

    def __init__(self, urllib3_stream: typing.Any) -> None:
        self._urllib3_stream = urllib3_stream

    def __iter__(self) -> typing.Iterator[bytes]:
        yield from self._urllib3_stream.stream(self.CHUNK_SIZE)

    def close(self) -> None:
        self._urllib3_stream.release_conn()


class Urllib3Transport(httpx.BaseTransport):
    def __init__(
        self,
        verify: VerifyTypes = True,
        trust_env: bool = True,
        max_pools: int = 10,
        maxsize: int = 10,
        cert: CertTypes | None = None,
        proxy: ProxyTypes | None = None,
    ) -> None:
        ssl_context = create_ssl_context(cert=cert, verify=verify, trust_env=trust_env)
        proxy = httpx.Proxy(url=proxy) if isinstance(proxy, (str, httpx.URL)) else proxy

        if proxy is None:
            self._pool = urllib3.PoolManager(
                ssl_context=ssl_context,
                num_pools=max_pools,
                maxsize=maxsize,
                block=False,
            )
        elif proxy.url.scheme in ("http", "https"):
            self._pool = urllib3.ProxyManager(
                str(proxy.url.origin),
                num_pools=max_pools,
                maxsize=maxsize,
                block=False,
                proxy_ssl_context=proxy.ssl_context,
                proxy_headers=httpx_headers_to_urllib3_headers(proxy.headers),
                ssl_context=ssl_context,
            )
        elif proxy.url.scheme == "socks5":
            from urllib3.contrib.socks import SOCKSProxyManager

            username, password = proxy.auth or (None, None)

            self._pool = SOCKSProxyManager(
                proxy_url=str(proxy.url),
                num_pools=max_pools,
                maxsize=maxsize,
                block=False,
                username=username,
                password=password,
            )
        else:  # pragma: no cover
            raise ValueError(
                "Proxy protocol must be either 'http', 'https', or 'socks5',"
                f" but got {proxy.url.scheme!r}."
            )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        timeouts = request.extensions.get("timeout", {})

        connect_timeout = timeouts.get("connect", None)
        read_timeout = timeouts.get("read", None)

        urllib3_timeout = urllib3.Timeout(
            connect=connect_timeout,
            read=read_timeout,
        )

        response = self._pool.request(
            request.method,
            str(request.url),
            body=request.content,
            headers=httpx_headers_to_urllib3_headers(request.headers),
            redirect=False,
            preload_content=False,
            timeout=urllib3_timeout,
        )

        return httpx.Response(
            status_code=response.status,
            headers=httpx.Headers(list(response.headers.iteritems())),
            content=ResponseStream(response),
            extensions={"urllib3_response": response},
        )
