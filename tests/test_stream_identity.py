from types import SimpleNamespace

from starlette.requests import HTTPConnection

from app.core.limiter import stream_client_key


def test_shared_key_different_ips():
    app = SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(trust_proxy_headers=False)))
    def connection(ip):
        return HTTPConnection({"type": "websocket", "app": app,
                               "client": (ip, 1234), "headers": []})
    assert stream_client_key(connection("192.0.2.1"), "shared") != stream_client_key(
        connection("192.0.2.2"), "shared")
