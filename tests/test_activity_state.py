"""Offline tests for activity expiry and capability parsing."""

from __future__ import annotations

import importlib
import unittest
from unittest.mock import Mock, patch

from debug_tools.bootstrap import _bootstrap_integration_modules

_bootstrap_integration_modules()
IOT = importlib.import_module("custom_components.cloudplus.coordinator.iot")
STATE = importlib.import_module("custom_components.cloudplus.coordinator.state")
POLL = importlib.import_module("custom_components.cloudplus.coordinator.activity")
GRACE = POLL.ALARM_POLL_INTERVAL + 5


class FakeCoordinator(STATE.CoordinatorStateMixin):
    def __init__(self, iot=None, motion_timeout=120):
        self._iot = iot or {}
        self._motion_timeout = motion_timeout
        self._activity_detected = True
        self._activity_type = "Noise"
        self._last_motion_time = 1000.0
        self._fire_activity = Mock()
        self._fire_update = Mock()

    def get_iot_value(self, code):
        return self._iot.get(code)


class ParseCapabilitiesTests(unittest.TestCase):
    def test_top_level_version_is_included(self):
        caps = IOT.parse_capabilities({"capability": '{"ver":85,"caps":{"afq":90}}'})
        self.assertEqual(caps, {"afq": 90, "ver": 85})

    def test_inner_version_wins(self):
        caps = IOT.parse_capabilities({"capability": '{"ver":85,"caps":{"ver":22}}'})
        self.assertEqual(caps["ver"], 22)

    def test_flat_capabilities_are_unchanged(self):
        self.assertEqual(IOT.parse_capabilities({"capability": {"afq": 6}}), {"afq": 6})

    def test_invalid_capabilities_are_empty(self):
        self.assertEqual(IOT.parse_capabilities({"capability": "not json"}), {})


class ActivityTimeoutTests(unittest.TestCase):
    def test_alarm_interval_is_used_with_grace(self):
        expected = {1: 60, 2: 120, 3: 180, 4: 300, 5: 600, 6: 30}
        for value, seconds in expected.items():
            with self.subTest(value=value):
                coord = FakeCoordinator({178: value})
                self.assertEqual(coord._activity_timeout(), seconds + GRACE)

    def test_string_value_from_cloud_is_accepted(self):
        self.assertEqual(FakeCoordinator({178: "6"})._activity_timeout(), 30 + GRACE)

    def test_unset_interval_falls_back_to_configured_timeout(self):
        for iot in ({}, {178: 0}, {178: 99}, {178: "bad"}):
            with self.subTest(iot=iot):
                coord = FakeCoordinator(iot, motion_timeout=90)
                self.assertEqual(coord._activity_timeout(), 90.0)


class ExpireActivityTests(unittest.TestCase):
    def expire_at(self, coord, now):
        with patch.object(STATE.time, "monotonic", return_value=now):
            coord._expire_activity()

    def test_stays_active_before_timeout(self):
        coord = FakeCoordinator({178: 6})
        self.expire_at(coord, 1000.0 + 30 + GRACE - 1)
        self.assertTrue(coord._activity_detected)
        self.assertEqual(coord._activity_type, "Noise")

    def test_clears_after_timeout(self):
        coord = FakeCoordinator({178: 6})
        self.expire_at(coord, 1000.0 + 30 + GRACE)
        self.assertFalse(coord._activity_detected)
        self.assertEqual(coord._activity_type, "")
        coord._fire_activity.assert_called_once()

    def test_fallback_timeout_applies_without_alarm_interval(self):
        coord = FakeCoordinator({}, motion_timeout=120)
        self.expire_at(coord, 1000.0 + 119)
        self.assertTrue(coord._activity_detected)
        self.expire_at(coord, 1000.0 + 120)
        self.assertFalse(coord._activity_detected)

    def test_idle_coordinator_is_untouched(self):
        coord = FakeCoordinator({178: 6})
        coord._activity_detected = False
        self.expire_at(coord, 1000.0 + 500)
        coord._fire_activity.assert_not_called()


if __name__ == "__main__":
    unittest.main()
