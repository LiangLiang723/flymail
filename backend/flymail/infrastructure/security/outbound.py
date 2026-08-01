"""Outbound endpoint validation that blocks internal and metadata networks."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence

from flymail.domain.errors import UnsafeEndpointError


EndpointResolver = Callable[[str, int], Sequence[str]]


def resolve_host(host: str, port: int) -> tuple[str, ...]:
    addresses: list[str] = []
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeEndpointError("endpoint host did not resolve") from exc
    for family, _socktype, _proto, _canonname, sockaddr in results:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise UnsafeEndpointError("endpoint host did not resolve")
    return tuple(addresses)


def validate_public_endpoint(
    host: str,
    port: int,
    *,
    resolver: EndpointResolver = resolve_host,
    allow_private: bool = False,
) -> str:
    normalized_host = str(host or "").strip().rstrip(".").casefold()
    if not normalized_host or any(character.isspace() for character in normalized_host):
        raise UnsafeEndpointError("endpoint host is invalid")
    normalized_port = int(port)
    if not 1 <= normalized_port <= 65535:
        raise UnsafeEndpointError("endpoint port is invalid")
    try:
        literal = ipaddress.ip_address(normalized_host)
        addresses = (str(literal),)
    except ValueError:
        addresses = tuple(str(value) for value in resolver(normalized_host, normalized_port))
    if not addresses:
        raise UnsafeEndpointError("endpoint host did not resolve")
    if not allow_private:
        try:
            if any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise UnsafeEndpointError("endpoint must resolve only to public addresses")
        except ValueError as exc:
            raise UnsafeEndpointError("endpoint resolver returned an invalid address") from exc
    return normalized_host
