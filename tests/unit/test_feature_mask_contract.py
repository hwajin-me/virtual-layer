"""Every advertised feature mask must reject invalid values consistently."""

from importlib import import_module
from unittest.mock import Mock

import pytest
import voluptuous as vol

from custom_components.virtual_layer import _state_only_native_template_value
from custom_components.virtual_layer.config_flow import DOMAIN_NATIVE_TEMPLATE_PROPERTIES, _platform_schema
from custom_components.virtual_layer.const import STATE_ONLY_ENTITY_DOMAINS
from custom_components.virtual_layer.generic import GenericVirtualEntity


@pytest.mark.parametrize("domain", [domain for domain, properties in DOMAIN_NATIVE_TEMPLATE_PROPERTIES.items()
                                     if "supported_features" in properties])
@pytest.mark.parametrize("value", [-1, True, 1.5, float("inf"), float("nan")])
def test_invalid_feature_masks_do_not_change_capabilities(hass, domain, value):
    if domain in STATE_ONLY_ENTITY_DOMAINS:
        with pytest.raises((ValueError, TypeError, vol.Invalid)):
            _state_only_native_template_value("supported_features", value)
        return
    module = import_module(f"custom_components.virtual_layer.{domain}")
    cls = getattr(module, "ENTITY_CLASS", None) or getattr(
        module, "Virtual" + "".join(part.title() for part in domain.split("_")), GenericVirtualEntity
    )
    config = _platform_schema(domain)({
        "name": "Capabilities", "entity_id": f"{domain}.audit",
        "initial_value": {"lawn_mower": "docked", "siren": "off"}.get(domain, "unknown"),
    })
    entity = cls(config, domain, False) if cls is GenericVirtualEntity else cls(config, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    before = entity.supported_features
    with pytest.raises((ValueError, TypeError, vol.Invalid)):
        entity._apply_native_template_value("supported_features", value)
    entity._native_templates_applied()
    assert entity.supported_features == before
