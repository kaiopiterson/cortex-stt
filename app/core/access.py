"""Network and credential checks for operational endpoints (AUD-10, AUD-11)."""

import secrets
from ipaddress import ip_address, ip_network

from starlette.requests import HTTPConnection

from app.core.limiter import client_ip


def network_allowed(connection: HTTPConnection) -> bool:
    """True when the client address is inside STT_ADMIN_ALLOWED_NETWORKS.

    The address honours STT_TRUST_PROXY_HEADERS, so behind the Cloudflare
    tunnel it is the real visitor IP. Connections without an IP address
    (Unix sockets, in-process transports) are local by definition.
    """
    settings = connection.app.state.settings
    if not settings.trust_proxy_headers and "cf-connecting-ip" in connection.headers:
        # Arrived through Cloudflare but proxy headers are not trusted: the socket
        # address is the tunnel container, not the visitor. Treat it as external.
        return False
    host = client_ip(connection).removeprefix("ip:")
    try:
        address = ip_address(host)
    except ValueError:
        return True
    networks = settings.admin_allowed_networks
    return any(address in ip_network(network, strict=False) for network in networks)


def bearer(connection: HTTPConnection) -> str:
    scheme, _, token = connection.headers.get("Authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" else ""


def matches(token: str, secret) -> bool:
    return secret is not None and secrets.compare_digest(
        token.encode("utf-8"), secret.get_secret_value().encode("utf-8")
    )


def admin_enabled(connection: HTTPConnection) -> bool:
    return connection.app.state.settings.admin_key is not None


def metrics_enabled(connection: HTTPConnection) -> bool:
    settings = connection.app.state.settings
    return settings.admin_key is not None or settings.metrics_token is not None


def actor(connection: HTTPConnection) -> str:
    return "admin@" + client_ip(connection).removeprefix("ip:")
