import pytest
import requests

from config import settings
from src import crypto, netguard
from src.services import scraper

HOSTS = {"public.example": "93.184.216.34", "localhost": "127.0.0.1", "meta.example": "169.254.169.254",
         "internal.example": "10.1.2.3", "v6.example": "::1", "mapped.example": "::ffff:10.0.0.1"}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    netguard._resolve.cache_clear()
    def gai(host, port, *a, **k):
        if host not in HOSTS:
            raise netguard.socket.gaierror("nope")
        return [(2, 1, 6, "", (HOSTS[host], 0))]
    monkeypatch.setattr(netguard.socket, "getaddrinfo", gai)


@pytest.mark.parametrize("url", [
    "http://localhost/admin", "http://127.0.0.1:8501", "http://meta.example/latest/meta-data",
    "http://internal.example/", "http://v6.example/", "http://mapped.example/", "file:///etc/passwd",
    "ftp://public.example/x", "http://user:pw@public.example/", "http://nonexistent.example/",
])
def test_unsafe_urls_rejected(url):
    with pytest.raises(netguard.UnsafeURL):
        netguard.assert_public_url(url)


def test_public_url_ok():
    assert netguard.assert_public_url("https://public.example/jobs/1")


class Resp:
    def __init__(self, status=200, headers=None, body=b"hello"):
        self.status_code, self.headers, self._body = status, headers or {}, body
        self.is_redirect = status in (301, 302)
    def iter_content(self, n): yield self._body
    def close(self): pass


def test_redirect_to_internal_is_blocked(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, **k: Resp(302, {"Location": "http://meta.example/secret"})
                        if "public" in url else Resp())
    with pytest.raises(netguard.UnsafeURL):
        netguard.safe_get("http://public.example/start")


def test_safe_get_follows_safe_redirect_and_caps_size(monkeypatch):
    seen = []
    def get(url, **k):
        seen.append(url)
        return Resp(302, {"Location": "/final"}) if url.endswith("start") else Resp(body=b"x" * 5000)
    monkeypatch.setattr(requests, "get", get)
    r = netguard.safe_get("http://public.example/start", max_bytes=1000)
    assert seen == ["http://public.example/start", "http://public.example/final"] and len(r._content) <= 65536


def test_scraper_reports_unsafe_as_scrape_error():
    with pytest.raises(scraper.ScrapeError, match="private network"):
        scraper.scrape_job_posting("http://internal.example/job")


def test_browser_route_guard():
    calls = []
    class Route:
        def __init__(s, url): s.request = type("Q", (), {"url": url})()
        def abort(s): calls.append("abort")
        def continue_(s): calls.append("ok")
    guard = netguard.route_guard()
    for u in ("https://public.example/x", "http://meta.example/x", "data:text/plain,hi", "http://localhost:8501"):
        guard(Route(u))
    assert calls == ["ok", "abort", "ok", "abort"]


# ---------------------------------------------------------------- encryption
def test_encryption_roundtrip_and_rotation(monkeypatch):
    from cryptography.fernet import Fernet
    k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", k1)
    token = crypto.encrypt("my resume")
    assert token.startswith("enc1:") and "my resume" not in token
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", f"{k2},{k1}")        # rotate: new key first, old still decrypts
    assert crypto.decrypt(token) == "my resume"
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", k2)
    assert crypto.decrypt(crypto.encrypt("x")) == "x"
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token)                                            # old key removed: loud failure, not garbage


def test_plaintext_passthrough_and_production_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "")
    assert crypto.encrypt("abc") == "abc" and crypto.decrypt("abc") == "abc" and crypto.encrypt("") == ""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(crypto.CryptoError):
        crypto.require_key_in_production()
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt("enc1:abcd")
