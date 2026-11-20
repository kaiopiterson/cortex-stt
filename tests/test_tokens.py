import pytest

from app.core.registry import AppRegistry


@pytest.fixture
def registered(tmp_path):
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    app_id, key = registry.register("Ritmo")
    return registry, app_id, key


def test_expiry(registered, monkeypatch):
    registry, app_id, key = registered
    monkeypatch.setattr("app.core.registry.time.time", lambda: 1000)
    token, ttl = registry.issue_token(key, 10)
    assert ttl == 10
    assert registry.identify(token) == app_id
    assert registry.issue_token(token) is None
    monkeypatch.setattr("app.core.registry.time.time", lambda: 1010)
    assert registry.identify(token) is None
    assert registry.identify(key) == app_id


def test_rotation_and_revocation(registered):
    registry, app_id, key = registered
    token, _ = registry.issue_token(key)
    new_key = registry.rotate(app_id)
    assert registry.identify(key) is None
    assert registry.identify(token) is None
    assert registry.identify(new_key) == app_id
    token, _ = registry.issue_token(new_key)
    registry.revoke_tokens(app_id)
    assert registry.identify(token) is None
    assert registry.identify(new_key) == app_id
    token, _ = registry.issue_token(new_key)
    registry.revoke_app(app_id)
    assert registry.identify(token) is None
    assert registry.identify(new_key) is None
    assert registry.issue_token(new_key) is None
    with pytest.raises(ValueError):
        registry.rotate(app_id)


def test_invalid_exchange(registered):
    registry, _, key = registered
    assert registry.issue_token("x" * 40) is None
    assert registry.issue_token(key, 0) is None
    assert registry.issue_token(key, 3601) is None
