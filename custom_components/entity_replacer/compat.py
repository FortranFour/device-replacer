"""Use the schema library actually used by this Home Assistant runtime."""

from importlib import import_module

from homeassistant.components import websocket_api

_library = type(websocket_api.BASE_COMMAND_MESSAGE_SCHEMA).__module__.split(".", 1)[0]
if _library not in {"probatio", "voluptuous"}:
    # Older runtimes use voluptuous; refuse to guess about future libraries.
    raise ImportError(f"Unsupported Home Assistant schema library: {_library}")
vol = import_module(_library)
