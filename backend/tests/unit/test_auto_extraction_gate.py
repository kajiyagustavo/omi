"""Unit tests for the auto-extraction kill-switch gate (OMI_AUTO_EXTRACTION_ENABLED)."""

from utils.conversations.extraction_gate import auto_extraction_enabled


def test_default_true(monkeypatch):
    """When env var is absent, auto-extraction is enabled by default."""
    monkeypatch.delenv('OMI_AUTO_EXTRACTION_ENABLED', raising=False)
    assert auto_extraction_enabled() is True


def test_false_disables(monkeypatch):
    """Value 'false' disables auto-extraction."""
    monkeypatch.setenv('OMI_AUTO_EXTRACTION_ENABLED', 'false')
    assert auto_extraction_enabled() is False


def test_false_case_insensitive(monkeypatch):
    """Value 'FALSE' (uppercase) also disables auto-extraction."""
    monkeypatch.setenv('OMI_AUTO_EXTRACTION_ENABLED', 'FALSE')
    assert auto_extraction_enabled() is False


def test_false_with_whitespace(monkeypatch):
    """Surrounding whitespace is stripped before comparison."""
    monkeypatch.setenv('OMI_AUTO_EXTRACTION_ENABLED', '  false  ')
    assert auto_extraction_enabled() is False


def test_true_stays_enabled(monkeypatch):
    """Value 'true' keeps auto-extraction enabled."""
    monkeypatch.setenv('OMI_AUTO_EXTRACTION_ENABLED', 'true')
    assert auto_extraction_enabled() is True


def test_yes_stays_enabled(monkeypatch):
    """Non-'false' value like 'yes' keeps auto-extraction enabled."""
    monkeypatch.setenv('OMI_AUTO_EXTRACTION_ENABLED', 'yes')
    assert auto_extraction_enabled() is True
