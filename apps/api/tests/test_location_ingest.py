from __future__ import annotations

import unittest
from datetime import UTC, datetime

from cargoflow_api.location_ingest import (
    DeviceEventError,
    DeviceEventStore,
    DeviceTaskBinding,
)


def device_event(
    event_id: str,
    *,
    event_type: str = "gps",
    occurred_at: str = "2026-05-13T10:00:00+00:00",
    reported_at: str = "2026-05-13T10:00:03+00:00",
    longitude: float | None = 121.4737,
    latitude: float | None = 31.2304,
    task_id: str = "task-1",
    device_id: str = "gps-1",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "eventId": event_id,
        "eventType": event_type,
        "deviceId": device_id,
        "taskId": task_id,
        "occurredAt": occurred_at,
        "reportedAt": reported_at,
        "schemaVersion": 1,
    }
    if longitude is not None:
        payload["longitude"] = longitude
    if latitude is not None:
        payload["latitude"] = latitude
    return payload


class DeviceEventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = DeviceEventStore(
            (
                DeviceTaskBinding(
                    device_id="gps-1",
                    task_id="task-1",
                    vehicle_id="vehicle-1",
                ),
            )
        )

    def test_gps_event_updates_latest_location(self) -> None:
        result = self.store.ingest(device_event("evt-gps-1"))

        self.assertTrue(result.received)
        self.assertTrue(result.latest_location_updated)
        latest = self.store.latest_location("task-1")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.event_id, "evt-gps-1")
        self.assertEqual(latest.captured_at, datetime(2026, 5, 13, 10, tzinfo=UTC))

    def test_heartbeat_and_box_events_are_received_without_location_update(self) -> None:
        heartbeat = self.store.ingest(
            device_event(
                "evt-heartbeat-1",
                event_type="heartbeat",
                longitude=None,
                latitude=None,
            )
        )
        box_opened = self.store.ingest(
            device_event(
                "evt-box-1",
                event_type="box_opened",
                longitude=None,
                latitude=None,
            )
        )

        self.assertTrue(heartbeat.received)
        self.assertTrue(box_opened.received)
        self.assertFalse(heartbeat.latest_location_updated)
        self.assertFalse(box_opened.latest_location_updated)
        self.assertIsNone(self.store.latest_location("task-1"))

    def test_invalid_coordinates_do_not_overwrite_latest_location(self) -> None:
        self.store.ingest(device_event("evt-gps-1"))

        result = self.store.ingest(
            device_event(
                "evt-gps-2",
                occurred_at="2026-05-13T10:01:00+00:00",
                reported_at="2026-05-13T10:01:02+00:00",
                latitude=95,
            )
        )

        latest = self.store.latest_location("task-1")
        self.assertFalse(result.latest_location_updated)
        self.assertEqual(result.ignored_reason, "invalid_coordinates")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.event_id, "evt-gps-1")

    def test_abnormal_capture_time_does_not_overwrite_latest_location(self) -> None:
        self.store.ingest(device_event("evt-gps-1"))

        result = self.store.ingest(
            device_event(
                "evt-gps-2",
                occurred_at="2026-05-13T10:10:00+00:00",
                reported_at="2026-05-13T10:00:00+00:00",
                longitude=121.5,
                latitude=31.3,
            )
        )

        latest = self.store.latest_location("task-1")
        self.assertFalse(result.latest_location_updated)
        self.assertEqual(result.ignored_reason, "abnormal_capture_time")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.event_id, "evt-gps-1")

    def test_stale_capture_time_does_not_overwrite_latest_location(self) -> None:
        self.store.ingest(device_event("evt-gps-1"))

        result = self.store.ingest(
            device_event(
                "evt-gps-2",
                occurred_at="2026-05-13T09:59:00+00:00",
                reported_at="2026-05-13T10:01:00+00:00",
                longitude=121.5,
                latitude=31.3,
            )
        )

        latest = self.store.latest_location("task-1")
        self.assertFalse(result.latest_location_updated)
        self.assertEqual(result.ignored_reason, "stale_capture_time")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.event_id, "evt-gps-1")

    def test_duplicate_event_is_idempotent(self) -> None:
        self.store.ingest(device_event("evt-gps-1"))

        result = self.store.ingest(device_event("evt-gps-1"))

        self.assertFalse(result.latest_location_updated)
        self.assertEqual(result.ignored_reason, "duplicate_event")

    def test_unknown_device_and_task_binding_are_rejected(self) -> None:
        with self.assertRaises(DeviceEventError):
            self.store.ingest(device_event("evt-gps-1", device_id="gps-other"))

        with self.assertRaises(DeviceEventError):
            self.store.ingest(device_event("evt-gps-2", task_id="task-other"))


if __name__ == "__main__":
    unittest.main()
