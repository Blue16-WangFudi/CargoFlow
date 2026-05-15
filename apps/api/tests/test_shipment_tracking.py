from __future__ import annotations

import unittest
from datetime import UTC, datetime

from cargoflow_api.access_control import AuthorizationError, Principal, Role
from cargoflow_api.domain import StatusReportState, TransportTaskStatus
from cargoflow_api.eta import EtaService
from cargoflow_api.location_ingest import DeviceEventStore, DeviceTaskBinding
from cargoflow_api.shipment_tracking import (
    ShipmentTrackingError,
    ShipmentTrackingRecord,
    ShipmentTrackingStore,
    TrajectoryStatusReportPoint,
    VehicleSummary,
)


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

    def test_eta_payload_returns_available_estimate_for_in_transit_task(self) -> None:
        payload = self.tracking.eta_payload(
            "CGF-DEMO-001",
            self.principal,
            DeviceEventStore.demo(),
            EtaService(average_speed_kph=60),
            now=datetime(2026, 5, 13, 10, 5, tzinfo=UTC),
        )

        eta = payload["eta"]
        self.assertEqual(payload["transportStatus"], "in_transit")
        self.assertEqual(eta["status"], "available")
        self.assertEqual(eta["updatedAt"], "2026-05-13T10:00:03+00:00")
        self.assertEqual(eta["calculatedAt"], "2026-05-13T10:05:00+00:00")
        self.assertEqual(eta["estimatedArrival"], "2026-05-13T10:22:28+00:00")
        self.assertEqual(eta["remainingDistanceKm"], 17.46)
        self.assertEqual(eta["destination"]["name"], "Shanghai Waigaoqiao Logistics Park")

    def test_eta_payload_reports_unavailable_when_location_is_missing(self) -> None:
        device_events = DeviceEventStore(
            (
                DeviceTaskBinding(
                    device_id="gps-demo-001",
                    task_id="task-demo-001",
                    vehicle_id="vehicle-demo-001",
                ),
            )
        )

        payload = self.tracking.eta_payload(
            "CGF-DEMO-001",
            self.principal,
            device_events,
            EtaService(),
            now=datetime(2026, 5, 13, 10, 5, tzinfo=UTC),
        )

        eta = payload["eta"]
        self.assertEqual(eta["status"], "unavailable")
        self.assertEqual(eta["reason"], "missing_location")
        self.assertIsNone(eta["estimatedArrival"])
        self.assertIsNone(eta["remainingDistanceKm"])
        self.assertEqual(eta["destination"]["name"], "Shanghai Waigaoqiao Logistics Park")

    def test_eta_payload_reports_unavailable_when_destination_is_missing(self) -> None:
        record = ShipmentTrackingRecord(
            shipment_id="CGF-NO-DEST",
            cargo_id="cargo-no-dest",
            task_id="task-demo-001",
            tenant_id="cgf-demo",
            owner_user_id="owner-acme",
            driver_user_id="driver-demo",
            warehouse_ids=("warehouse-shanghai",),
            dispatch_region_ids=("east-china",),
            transport_status=TransportTaskStatus.IN_TRANSIT,
            vehicle=VehicleSummary(
                vehicle_id="vehicle-demo-001",
                vehicle_number="VH-DEMO-001",
                plate_number="CF-2026",
                device_id="gps-demo-001",
                driver_user_id="driver-demo",
            ),
        )
        tracking = ShipmentTrackingStore((record,))

        payload = tracking.eta_payload(
            "CGF-NO-DEST",
            self.principal,
            DeviceEventStore.demo(),
            EtaService(),
            now=datetime(2026, 5, 13, 10, 5, tzinfo=UTC),
        )

        eta = payload["eta"]
        self.assertEqual(eta["status"], "unavailable")
        self.assertEqual(eta["reason"], "missing_destination")
        self.assertIsNone(eta["destination"])

    def test_trajectory_payload_returns_ordered_replay_points_and_key_nodes(self) -> None:
        device_events = DeviceEventStore.demo()
        device_events.ingest(
            {
                "eventId": "evt-demo-gps-2",
                "eventType": "gps",
                "deviceId": "gps-demo-001",
                "taskId": "task-demo-001",
                "occurredAt": "2026-05-13T10:08:00+00:00",
                "reportedAt": "2026-05-13T10:08:03+00:00",
                "schemaVersion": 1,
                "longitude": 121.5,
                "latitude": 31.24,
            }
        )
        device_events.ingest(
            {
                "eventId": "evt-demo-box-opened",
                "eventType": "box_opened",
                "deviceId": "gps-demo-001",
                "taskId": "task-demo-001",
                "occurredAt": "2026-05-13T10:09:00+00:00",
                "reportedAt": "2026-05-13T10:09:02+00:00",
                "schemaVersion": 1,
            }
        )

        payload = self.tracking.trajectory_payload(
            "CGF-DEMO-001",
            self.principal,
            device_events,
        )

        kinds = [point["kind"] for point in payload["trajectory"]]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds[-1], "end")
        self.assertLess(kinds.index("status_report"), kinds.index("gps"))
        self.assertEqual(kinds.count("gps"), 2)
        self.assertGreaterEqual(kinds.count("alert"), 2)
        self.assertEqual(payload["summary"]["gpsPointCount"], 2)
        self.assertEqual(payload["summary"]["statusReportCount"], 1)
        self.assertTrue(payload["summary"]["hasStartPoint"])
        self.assertTrue(payload["summary"]["hasEndPoint"])
        self.assertFalse(payload["summary"]["isSimplified"])
        key_nodes = [point for point in payload["trajectory"] if point["isKeyNode"]]
        self.assertIn("alert-demo-box-001", {point.get("alertId") for point in key_nodes})
        self.assertIn("evt-demo-box-opened", {point.get("alertId") for point in key_nodes})

    def test_trajectory_payload_preserves_status_report_without_gps_points(self) -> None:
        record = ShipmentTrackingRecord(
            shipment_id="CGF-STATUS-ONLY",
            cargo_id="cargo-status-only",
            task_id="task-status-only",
            tenant_id="cgf-demo",
            owner_user_id="owner-acme",
            driver_user_id="driver-demo",
            warehouse_ids=("warehouse-shanghai",),
            dispatch_region_ids=("east-china",),
            transport_status=TransportTaskStatus.LOADED,
            vehicle=VehicleSummary(
                vehicle_id="vehicle-status-only",
                vehicle_number="VH-STATUS-ONLY",
                plate_number="CF-2027",
                device_id="gps-status-only",
                driver_user_id="driver-demo",
            ),
            status_reports=(
                TrajectoryStatusReportPoint(
                    report_id="report-loaded",
                    report_status=StatusReportState.LOADED,
                    reporter_user_id="driver-demo",
                    reported_at=datetime(2026, 5, 13, 9, 55, tzinfo=UTC),
                ),
            ),
        )
        tracking = ShipmentTrackingStore((record,))
        device_events = DeviceEventStore(
            (
                DeviceTaskBinding(
                    device_id="gps-status-only",
                    task_id="task-status-only",
                    vehicle_id="vehicle-status-only",
                ),
            )
        )

        payload = tracking.trajectory_payload(
            "CGF-STATUS-ONLY",
            self.principal,
            device_events,
        )

        self.assertEqual(payload["summary"]["gpsPointCount"], 0)
        self.assertEqual(payload["trajectory"][0]["kind"], "status_report")


if __name__ == "__main__":
    unittest.main()
