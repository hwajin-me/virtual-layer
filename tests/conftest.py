"""Shared pytest configuration for Virtual Layer tests."""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load custom integrations in tests."""
    yield


@pytest.fixture(autouse=True)
def isolate_virtual_layer_metadata(tmp_path, monkeypatch):
    """Keep real metadata I/O independent across tests and pytest processes."""
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(tmp_path / "virtual_layer.meta.json"),
    )
