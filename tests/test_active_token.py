from app.core.registry import AppRegistry


def test_active_token_survives_expiry_and_new_issuance(tmp_path, monkeypatch):
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    app_id, key = registry.register("Ritmo")
    monkeypatch.setattr("app.core.registry.time.time", lambda: 1000)
    issued = registry.issue_token(key, ttl=1)
    assert issued is not None
    token, _ = issued
    monkeypatch.setattr("app.core.registry.time.time", lambda: 1002)
    assert registry.identify(token) is None
    assert registry.issue_token(key) is not None
    assert registry.identify(token, ignore_expiry=True) == app_id
    registry.rotate(app_id)
    assert registry.identify(token, ignore_expiry=True) is None
