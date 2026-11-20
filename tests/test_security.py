import pytest
from app.api.common import valid_language


@pytest.mark.parametrize("language", ["pt", "pt-BR", "en", "auto", ""])
def test_language_allowed(language):
    assert valid_language(language)


@pytest.mark.parametrize("language", ["invalid", "x" * 33])
def test_language_rejected(language):
    assert not valid_language(language)
