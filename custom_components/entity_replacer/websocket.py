"""Administrator-only WebSocket commands for the sidebar panel."""

from __future__ import annotations

import logging

from homeassistant.components import websocket_api

from .compat import vol
from .const import DOMAIN
from .engine import ReplacementError

_LOGGER = logging.getLogger(__name__)


async def _respond(hass, connection, msg, action: str) -> None:
    manager = hass.data.get(DOMAIN, {}).get("manager")
    if manager is None:
        connection.send_error(msg["id"], "not_loaded", "Device Replacer is not loaded.")
        return
    try:
        if action == "status":
            result = await manager.status()
        elif action == "scan":
            result = await manager.scan(
                connection.user.id, msg["source"].strip(), msg["target"].strip(),
                msg.get("include_text", True), msg.get("include_storage", True),
                msg.get("allow_unavailable_target", False),
            )
        elif action == "device_plan":
            result = await manager.device_plan(msg["source_device"].strip(), msg["target_device"].strip())
        elif action == "scan_device":
            result = await manager.scan_device(
                connection.user.id, msg["source_device"].strip(), msg["target_device"].strip(), msg["mappings"],
                msg.get("confirmed_mappings", False), msg.get("include_text", True), msg.get("include_storage", True),
                msg.get("allow_unavailable_target", False),
            )
        elif action == "apply":
            result = await manager.apply(connection.user.id, msg["preview_id"], msg["selected_ids"])
        elif action == "review":
            result = await manager.review(connection.user.id, msg["preview_id"], msg["selected_ids"])
        elif action == "statistics_preview":
            result = await manager.statistics_preview(connection.user.id, msg["source"].strip(), msg["target"].strip())
        elif action == "statistics_apply":
            result = await manager.statistics_apply(connection.user.id, msg["preview_id"], msg["confirmed"])
        elif action == "statistics_verify":
            result = await manager.statistics_verify(msg["job_id"])
        elif action == "statistics_disable":
            result = await manager.statistics_disable(connection.user.id, msg["preview_id"], msg["confirmed"])
        else:
            result = await manager.restore(msg["job_id"])
        connection.send_result(msg["id"], result)
    except ReplacementError as err:
        connection.send_error(msg["id"], "review_required", str(err))
    except Exception:
        _LOGGER.exception("Device Replacer %s failed", action)
        connection.send_error(msg["id"], "operation_failed", "The operation failed. Check the Home Assistant log and backup history before retrying.")


@websocket_api.websocket_command({vol.Required("type"): DOMAIN + "/status"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_status(hass, connection, msg):
    await _respond(hass, connection, msg, "status")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/scan",
    vol.Required("source"): str,
    vol.Required("target"): str,
    vol.Optional("include_text", default=True): bool,
    vol.Optional("include_storage", default=True): bool,
    vol.Optional("allow_unavailable_target", default=False): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan(hass, connection, msg):
    await _respond(hass, connection, msg, "scan")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/device_plan",
    vol.Required("source_device"): str,
    vol.Required("target_device"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_device_plan(hass, connection, msg):
    await _respond(hass, connection, msg, "device_plan")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/scan_device",
    vol.Required("source_device"): str,
    vol.Required("target_device"): str,
    vol.Required("mappings"): [{vol.Required("source"): str, vol.Optional("target"): str, vol.Optional("source_registry_id"): str}],
    vol.Required("confirmed_mappings"): bool,
    vol.Optional("include_text", default=True): bool,
    vol.Optional("include_storage", default=True): bool,
    vol.Optional("allow_unavailable_target", default=False): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_device(hass, connection, msg):
    await _respond(hass, connection, msg, "scan_device")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/apply",
    vol.Required("preview_id"): str,
    vol.Required("selected_ids"): [str],
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_apply(hass, connection, msg):
    await _respond(hass, connection, msg, "apply")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/review",
    vol.Required("preview_id"): str,
    vol.Required("selected_ids"): [str],
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_review(hass, connection, msg):
    await _respond(hass, connection, msg, "review")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/restore",
    vol.Required("job_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_restore(hass, connection, msg):
    await _respond(hass, connection, msg, "restore")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/statistics_preview",
    vol.Required("source"): str,
    vol.Required("target"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_statistics_preview(hass, connection, msg):
    await _respond(hass, connection, msg, "statistics_preview")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/statistics_apply",
    vol.Required("preview_id"): str,
    vol.Required("confirmed"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_statistics_apply(hass, connection, msg):
    await _respond(hass, connection, msg, "statistics_apply")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/statistics_verify",
    vol.Required("job_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_statistics_verify(hass, connection, msg):
    await _respond(hass, connection, msg, "statistics_verify")


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/statistics_disable",
    vol.Required("preview_id"): str,
    vol.Required("confirmed"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_statistics_disable(hass, connection, msg):
    await _respond(hass, connection, msg, "statistics_disable")


def register_commands(hass) -> None:
    for handler in (ws_status, ws_scan, ws_device_plan, ws_scan_device, ws_review, ws_apply, ws_restore,
                    ws_statistics_preview, ws_statistics_apply, ws_statistics_verify, ws_statistics_disable):
        websocket_api.async_register_command(hass, handler)
