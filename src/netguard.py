"""SSRF protection. The server fetches URLs users type (job postings, application forms), so it must
never be talked into reaching internal services, cloud metadata endpoints or localhost."""
import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import requests


class UnsafeURL(ValueError):
    pass


@lru_cache(maxsize=512)
def _resolve(host: str) -> tuple[str, ...]:
    try:
        return tuple({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror as e:
        raise UnsafeURL(f"Couldn't resolve {host}.") from e


def assert_public_host(host: str) -> None:
    if not host:
        raise UnsafeURL("That link has no host name.")
    for addr in _resolve(host):
        ip = ipaddress.ip_address(addr.split("%")[0])
        if getattr(ip, "ipv4_mapped", None):   # ::ffff:10.0.0.1 is really 10.0.0.1
            ip = ip.ipv4_mapped
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise UnsafeURL("That address points to a private network, which isn't allowed.")


def assert_public_url(url: str) -> str:
    parts = urlparse(url.strip())
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL("Only http and https links are allowed.")
    if parts.username or parts.password:
        raise UnsafeURL("Links with embedded credentials aren't allowed.")
    assert_public_host(parts.hostname or "")
    return url.strip()


def safe_get(url: str, headers: dict | None = None, timeout: int = 10,
             max_bytes: int = 2_000_000, max_redirects: int = 5) -> requests.Response:
    """GET that validates the URL and every redirect hop, and caps the body size."""
    for _ in range(max_redirects + 1):
        assert_public_url(url)
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=False, stream=True)
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("Location", ""))
            r.close()
            continue
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) >= max_bytes:
                break
        r._content, r._content_consumed = body, True
        return r
    raise UnsafeURL("Too many redirects.")


def route_guard():
    """Playwright route handler: abort any browser request that targets a private address."""
    def guard(route):
        url = route.request.url
        if not url.startswith(("http://", "https://")):
            return route.continue_()      # data:, blob:
        try:
            assert_public_url(url)
        except UnsafeURL:
            return route.abort()
        return route.continue_()
    return guard
