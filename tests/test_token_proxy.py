from fastapi.testclient import TestClient

from test_audit_regressions import build


def test_token_limits_isolate_apps_and_proxy_failures(tmp_path):
    app, registry = build(tmp_path, trust_proxy_headers=True)
    _, first = registry.register("first")
    _, second = registry.register("second")
    with TestClient(app) as client:
        def exchange(key, ip):
            return client.post(
                "/v1/auth/token",
                headers={"Authorization": "Bearer " + key, "CF-Connecting-IP": ip},
            ).status_code

        for _ in range(30):
            assert exchange(first, "192.0.2.1") == 200
        assert exchange(first, "192.0.2.2") == 429
        assert exchange(second, "192.0.2.1") == 200
        for _ in range(30):
            assert exchange("invalid", "192.0.2.1") == 401
        assert exchange("invalid", "192.0.2.1") == 429
        assert exchange(second, "192.0.2.2") == 200
