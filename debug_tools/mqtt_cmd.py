"""``debug.py mqtt`` command: run the motion MQTT listener and log connection drops."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import time

from .auth import _login_api_with_fallback
from .bootstrap import _bootstrap_integration_modules

_MISSING = object()
_POLL_CODES = ["1007", "1008", "1009", "167"]


def _flatten(value, prefix: str = "") -> dict[str, object]:
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = enumerate(value)
    else:
        return {prefix: value}
    out: dict[str, object] = {}
    for key, sub in items:
        out.update(_flatten(sub, f"{prefix}.{key}" if prefix else str(key)))
    return out


async def cmd_mqtt(args) -> int:
    mods = _bootstrap_integration_modules()
    api = _login_api_with_fallback(mods["api"].MeariApiClient, args)
    motion = importlib.import_module("custom_components.cloudplus.coordinator.motion")

    logging.getLogger("custom_components.cloudplus.coordinator.motion").setLevel(
        logging.DEBUG
    )
    listener = motion.MotionEventListener(api)
    state: dict[str, object] = {}
    original = listener._handle_payload  # pylint: disable=protected-access

    def show(label: str, data, prefix: str = "") -> None:
        flat = _flatten(data, prefix)
        changes = [
            (key, state.get(key, _MISSING), value)
            for key, value in flat.items()
            if state.get(key, _MISSING) != value
        ]
        state.update(flat)
        print(
            f"{time.strftime('%H:%M:%S')} {label} "
            f"({len(changes)} new/changed of {len(flat)})"
        )
        for key, old, new in changes:
            if old is _MISSING:
                print(f"  + {key} = {new!r}")
            else:
                print(f"  ~ {key}: {old!r} -> {new!r}")

    def traced(payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            print(f"{time.strftime('%H:%M:%S')} RAW {payload!r}")
            return original(payload)
        show("MESSAGE", data)
        return original(payload)

    show(
        "STARTUP mqtt",
        {
            "host": api.mqtt_host,
            "port": api.mqtt_port,
            "keepalive": api.mqtt_keepalive,
            "user_id": api.user_id,
            "topics": motion._event_topics(api),  # pylint: disable=protected-access
        },
        "startup.mqtt",
    )
    for dev_id, dev in api.devices.items():
        show(f"STARTUP device {dev_id}", dev, f"startup.device.{dev_id}")
        try:
            events = api.get_device_events(dev_id, time.strftime("%Y%m%d"))
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            print(f"  device {dev_id} events failed: {exc}")
            continue
        show(f"STARTUP events {dev_id}", events, f"startup.events.{dev_id}")

    def poll_iot(label: str) -> None:
        for dev_id, dev in api.devices.items():
            try:
                if label == "STARTUP":
                    iot = api.get_device_iot_config(dev["snNum"])
                else:
                    iot = api.get_device_iot_values(dev["snNum"], _POLL_CODES)
            except (OSError, RuntimeError, ValueError, KeyError) as exc:
                print(f"  device {dev_id} iot config failed: {exc}")
                continue
            show(f"{label} iot {dev_id}", iot, f"iot.{dev_id}")

    poll_iot("STARTUP")

    listener._handle_payload = traced  # pylint: disable=protected-access
    listener.start()
    start = time.monotonic()
    next_poll = start + args.iot_interval
    try:
        while args.duration <= 0 or time.monotonic() - start < args.duration:
            await asyncio.sleep(1)
            if args.iot_interval > 0 and time.monotonic() >= next_poll:
                next_poll += args.iot_interval
                await asyncio.to_thread(poll_iot, "POLL")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        listener.stop()
    print(f"Ran {time.monotonic() - start:.0f}s")
    return 0
