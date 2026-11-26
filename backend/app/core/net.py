"""Outbound URL checks for user-configured model services (SSRF guard)."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class OutboundUrlError(ValueError):
    """Raised when a user-supplied base_url must not be requested."""


_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_SIXTOFOUR = ipaddress.ip_network("2002::/16")
_TEREDO = ipaddress.ip_network("2001::/32")


def _embedded_ipv4(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | None:
    """Return the IPv4 address carried inside transition/embedded IPv6 forms."""
    if address.version != 6:
        return None
    value = int(address)
    if any(address in network for network in _NAT64):
        return ipaddress.IPv4Address(value & 0xFFFFFFFF)
    if address in _SIXTOFOUR:
        return ipaddress.IPv4Address((value >> 80) & 0xFFFFFFFF)
    if address in _TEREDO:
        return ipaddress.IPv4Address((value & 0xFFFFFFFF) ^ 0xFFFFFFFF)
    return None


def _resolve(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise OutboundUrlError("模型服务地址无法解析") from exc
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError:
            continue
    if not addresses:
        raise OutboundUrlError("模型服务地址无法解析")
    return addresses


def check_outbound_url(
    url: str,
    *,
    allow_private: bool = False,
    allowed_hosts: tuple[str, ...] = (),
) -> None:
    """Reject URLs that point at local/private/link-local/reserved addresses.

    ``allowed_hosts`` (when non-empty) is an allowlist of hostnames or domain
    suffixes. ``allow_private`` is only for development and tests.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise OutboundUrlError("模型服务地址格式不正确") from exc
    if parts.scheme not in ("http", "https") or not host:
        raise OutboundUrlError("模型服务地址必须是合法的 http(s) URL")
    if parts.username or parts.password:
        raise OutboundUrlError("模型服务地址不能包含用户名或密码")
    host = host.rstrip(".").lower()
    if allowed_hosts:
        if not any(host == item or host.endswith("." + item) for item in allowed_hosts):
            raise OutboundUrlError("模型服务地址不在允许的域名列表中")
        # An explicitly allowlisted host is trusted as-is.
        return
    if allow_private:
        return
    port = port or (443 if parts.scheme == "https" else 80)
    for address in _resolve(host, port):
        if not address.is_global or address.is_multicast:
            raise OutboundUrlError("模型服务地址不能指向本机、内网或保留地址")
        embedded = _embedded_ipv4(address)
        if embedded is not None and not embedded.is_global:
            raise OutboundUrlError("模型服务地址不能指向本机、内网或保留地址")