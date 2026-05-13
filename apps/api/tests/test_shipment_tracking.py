from __future__ import annotations

import unittest
from datetime import UTC, datetime

from cargoflow_api.access_control import AuthorizationError, Principal, Role
from cargoflow_api.location_ingest import DeviceEventStore, DeviceTaskBinding
from cargoflow_api.shipment_tracking import ShipmentTrackingError, ShipmentTrackingStore


class ShipmentTrackingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracking = ShipmentTrackingStore.demo()
        self.principal = Principal("owner-acme", Role.CARGO_OWNER, "cgf-demo")

    def test_latest_location_payload_returns_bound_cargo_tracking_data(self) -> None:
        payload = self.tracking.latest_location_payload(
            "CGF-DEMO-001",
            self.principal,
            DeviceEventStore.demo(),
            now=datetime(2026, 5, 13, 10, 5, tzinfo=UTC),
        )

        self.assertEqual(payload["shipmentId"], "CGF-DEMO-001")
        self.assertEqual(payload["cargoId"], "cargo-demo-001")
        self.assertEqual(payload["taskId"], "task-demo-001")
        self.assertEqual(payload["transportStatus"], "in_transit")
        self.assertEqual(payload["vehicle"]["plateNumber"], "CF-2026")
        self.assertEqual(payload["latestLocation"]["eventId"], "evt-demo-seed-location")
        self.assertEqual(payload["latestLocation"]["updatedAt"], "2026-05-13T10:00:03+00:00")
        self.assertFalse(payload["delayHint"]["isDelayed"])

    def test_demo_alias_uses_same_tracking_record(self) -> None:
        payload = self.tracking.latest_location_payload(
            "demo",
            self.principal,
            DeviceEventStore.demo(),
            now=datetime(2026, 5, 13, 10, 5, tzinfo=UTC),
        )

        self.assertEqual(payload["shipmentId"], "CGF-DEMO-001")

    def test_owner_cannot_read_another_owner_shipment(self) -> None:
        principal = Principal("owner-other", Role.CARGO_OWNER, "cgf-demo")

        with self.assertRaises(AuthorizationError):
            self.tracking.latest_location_payload(
                "CGF-DEMO-001",
                principal,
                DeviceEventStore.demo(),
            )

    def test_missing_location_returns_delay_hint_without_fabricating_coordinates(self) -> None:
        device_events = DeviceEventStore(
            (
                DeviceTaskBinding(
                    device_id="gps-demo-001",
                    task_id="task-demo-001",
                    vehicle_id="vehicle-demo-001",
                ),
            )
        )

        payload = self.tracking.latest_location_payload(
            "CGF-DEMO-001",
            self.principal,
            device_events,
            now=datetime(2026, 5, 13, 10, 5, tzinfo=UTC),
        )

        self.assertIsNone(payload["latestLocation"])
        self.assertTrue(payload["delayHint"]["isDelayed"])
        self.assertEqual(payload["delayHint"]["status"], "missing")

    def test_unknown_shipment_is_not_found(self) -> None:
        with self.assertRaises(ShipmentTrackingError) as context:
            self.tracking.latest_location_payload(
                "CGF-OTHER",
                self.principal,
                DeviceEventStore.demo(),
            )

        self.assertEqual(context.exception.error_code, "shipment_not_found")


if __name__ == "__main__":
    unittest.main()
