"""SSRF-guarded HTTP GET for scraping third-party content.

Add-on ingestion follows URLs that originate from untrusted upstream data:
Modrinth modpack manifests, crawled page links, and ``<script src>`` targets.
Without a guard, a malicious or compromised source could point those fetches at
internal services (cloud metadata at 169.254.169.254, localhost, the Qdrant/NATS
containers, etc.).

``safe_get`` rejects non-HTTP(S) schemes and any URL whose host resolves to a
private, loopback, link-local, multicast, reserved, or unspecified address, and
it re-validates every redirect hop the same way (redirects are followed
manually rather than by the HTTP library).

Residual risk: a DNS-rebinding attacker could return a public address at
validation time and a private one when the socket actually connects. Fully
closing that requires pinning the connection to the validated IP. For this
project's threat model — ingestion triggered by the authenticated ``sync`` role
over operator-configured sources — host/IP allow-listing plus manual redirect
validation is a proportionate mitigation.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 5
_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """Raised when a URL uses a disallowed scheme or resolves to a non-public IP."""


def _is_public_ip(raw_ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(raw_ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _resolve_ips(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host {host!r}: {exc}") from exc
    return [info[4][0] for info in infos]


def validate_public_url(url: str) -> None:
    """Raise UnsafeURLError unless url is http(s) and every resolved IP is public."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"disallowed scheme in {url!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"missing host in {url!r}")
    ips = _resolve_ips(host)
    if not ips:
        raise UnsafeURLError(f"no addresses for host {host!r}")
    for ip in ips:
        if not _is_public_ip(ip):
            raise UnsafeURLError(f"host {host!r} resolves to non-public address {ip}")


def safe_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    **kwargs,
) -> requests.Response:
    """requests.get with SSRF guards and manually validated redirects.

    ``allow_redirects`` is forced off internally; each hop's Location is
    re-validated before being fetched.
    """
    kwargs.pop("allow_redirects", None)
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        validate_public_url(current)
        resp = requests.get(
            current,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
            **kwargs,
        )
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        return resp
    raise UnsafeURLError(f"too many redirects starting from {url!r}")
