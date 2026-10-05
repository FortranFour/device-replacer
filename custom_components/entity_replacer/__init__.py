"""Device Replacer: review, replace, and restore Home Assistant references."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PANEL_NAME, PANEL_PATH, STATIC_PATH, VERSION
from .manager import EntityReplacer
from .websocket import register_commands


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.title == "Entity Replacer":
        hass.config_entries.async_update_entry(entry, title="Device Replacer")
    data = hass.data.setdefault(DOMAIN, {})
    if not data.get("static_registered"):
        await hass.http.async_register_static_paths([
            StaticPathConfig(STATIC_PATH, str(Path(__file__).parent / "frontend"), False),
        ])
        data["static_registered"] = True
    register_commands(hass)
    data["manager"] = EntityReplacer(hass)
    if frontend.async_panel_exists(hass, PANEL_PATH):
        frontend.async_remove_panel(hass, PANEL_PATH)
    await panel_custom.async_register_panel(
        hass=hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name=PANEL_NAME,
        sidebar_title="Device Replacer",
        sidebar_icon="mdi:swap-horizontal",
        module_url=f"{STATIC_PATH}/panel.js?v={VERSION}",
        embed_iframe=False,
        require_admin=True,
        config_panel_domain=DOMAIN,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = hass.data.get(DOMAIN, {}).get("manager")
    if manager is not None and manager.lock.locked():
        return False
    frontend.async_remove_panel(hass, PANEL_PATH)
    hass.data.get(DOMAIN, {}).pop("manager", None)
    return True
