import importlib

def test_production_disables_documentation(monkeypatch):
    monkeypatch.setenv("STT_API_KEY", "test-only-" + "x" * 40)
    monkeypatch.setenv("STT_ENVIRONMENT", "production")
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        module = importlib.import_module("app.main")
        application = module.create_app()
        assert application.docs_url is None
        assert application.redoc_url is None
        assert application.openapi_url is None
    finally:
        get_settings.cache_clear()
