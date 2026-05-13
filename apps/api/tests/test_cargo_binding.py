from __future__ import annotations

import unittest

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.cargo_binding import (
    CargoBindingConflictError,
    CargoBindingError,
    CargoBindingStore,
)
from cargoflow_api.domain import Vehicle, VehicleBindingStatus
from cargoflow_api.location_ingest import DeviceEventError, DeviceEventStore
from cargoflow_api.shipment_tracking import ShipmentTrackingStore
from cargoflow_api.vehicle_management import VehicleStore


WAREHOUSE_ADMIN = Principal(
    "warehouse-admin-1",
    Role.WAREHOUSE_ADMIN,
    "cgf-demo",
    warehouse_ids=("warehouse-shanghai",),
)


class CargoBindingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bindings = CargoBindingStore.demo()
        self.vehicles = VehicleStore(
            (
                Vehicle(
                    id="vehicle-available-001",
                    vehicle_number="VH-AVAILABLE-001",
                    plate_number="SH-BIND-001",
                    device_id="gps-bind-001",
                    driver_user_id="driver-bind",
                ),
                Vehicle(
                    id="vehicle-available-002",
                    vehicle_number="VH-AVAILABLE-002",
                    plate_number="SH-BIND-002",
                    device_id="gps-bind-002",
                    driver_user_id="driver-two",
                ),
            )
        )
        self.device_events = DeviceEventStore(())
        self.tracking = ShipmentTrackingStore(())

    def test_binding_creates_task_and_links_future_locations_to_shipment(self) -> None:
        result = self.bindings.bind_cargo_to_vehicle(
            {
                "cargoId": "cargo-pending-001",
                "vehicleId": "vehicle-available-001",
            },
            WAREHOUSE_ADMIN,
            vehicles=self.vehicles,
            device_events=self.device_events,
            shipment_tracking=self.tracking,
        )

        self.assertTrue(result.created)
        self.assertEqual(result.task.cargo_id, "cargo-pending-001")
        self.assertEqual(result.task.vehicle_id, "vehicle-available-001")
        self.assertEqual(result.vehicle["bindingStatus"], "bound")

        event = self.device_events.ingest(
            {
                "eventId": "evt-bound-gps-1",
                "eventType": "gps",
                "deviceId": "gps-bind-001",
                "taskId": result.task.id,
                "occurredAt": "2026-05-13T10:05:00+00:00",
                "reportedAt": "2026-05-13T10:05:02+00:00",
                "schemaVersion": 1,
                "longitude": 121.1,
                "latitude": 31.1,
            }
        )
        self.assertTrue(event.latest_location_updated)

        payload = self.tracking.latest_location_payload(
            "CGF-PENDING-001",
            Principal("owner-beta", Role.CARGO_OWNER, "cgf-demo"),
            self.device_events,
        )
        self.assertEqual(payload["cargoId"], "cargo-pending-001")
        self.assertEqual(payload["latestLocation"]["eventId"], "evt-bound-gps-1")
        self.assertEqual(payload["vehicle"]["deviceId"], "gps-bind-001")

    def test_rebinding_existing_cargo_updates_task_and_device_binding(self) -> None:
        first = self.bindings.bind_cargo_to_vehicle(
            {
                "cargoId": "cargo-pending-001",
                "vehicleId": "vehicle-available-001",
            },
            WAREHOUSE_ADMIN,
            vehicles=self.vehicles,
            device_events=self.device_events,
            shipment_tracking=self.tracking,
        )

        second = self.bindings.bind_cargo_to_vehicle(
            {
                "cargoId": "cargo-pending-001",
                "vehicleId": "vehicle-available-002",
            },
            WAREHOUSE_ADMIN,
            vehicles=self.vehicles,
            device_events=self.device_events,
            shipment_tracking=self.tracking,
        )

        self.assertFalse(second.created)
        self.assertEqual(second.task.id, first.task.id)
        self.assertEqual(second.task.vehicle_id, "vehicle-available-002")
        old_vehicle = self.vehicles.get_vehicle("vehicle-available-001", WAREHOUSE_ADMIN)
        self.assertEqual(old_vehicle.binding_status, VehicleBindingStatus.AVAILABLE)

        with self.assertRaisesRegex(DeviceEventError, "active task"):
            self.device_events.ingest(
                {
                    "eventId": "evt-old-device",
                    "eventType": "gps",
                    "deviceId": "gps-bind-001",
                    "taskId": first.task.id,
                    "occurredAt": "2026-05-13T10:05:00+00:00",
                    "reportedAt": "2026-05-13T10:05:02+00:00",
                    "schemaVersion": 1,
                    "longitude": 121.1,
                    "latitude": 31.1,
                }
            )

        event = self.device_events.ingest(
            {
                "eventId": "evt-new-device",
                "eventType": "gps",
                "deviceId": "gps-bind-002",
                "taskId": first.task.id,
                "occurredAt": "2026-05-13T10:06:00+00:00",
                "reportedAt": "2026-05-13T10:06:02+00:00",
                "schemaVersion": 1,
                "longitude": 121.2,
                "latitude": 31.2,
            }
        )
        self.assertTrue(event.latest_location_updated)

    def test_rebinding_clears_previous_vehicle_latest_location(self) -> None:
        first = self.bindings.bind_cargo_to_vehicle(
            {
                "cargoId": "cargo-pending-001",
                "vehicleId": "vehicle-available-001",
            },
            WAREHOUSE_ADMIN,
            vehicles=self.vehicles,
            device_events=self.device_events,
            shipment_tracking=self.tracking,
        )
        self.device_events.ingest(
            {
                "eventId": "evt-first-vehicle",
                "eventType": "gps",
                "deviceId": "gps-bind-001",
                "taskId": first.task.id,
                "occurredAt": "2026-05-13T10:05:00+00:00",
                "reportedAt": "2026-05-13T10:05:02+00:00",
                "schemaVersion": 1,
                "longitude": 121.1,
                "latitude": 31.1,
            }
        )

        second = self.bindings.bind_cargo_to_vehicle(
            {
                "cargoId": "cargo-pending-001",
                "vehicleId": "vehicle-available-002",
            },
            WAREHOUSE_ADMIN,
            vehicles=self.vehicles,
            device_events=self.device_events,
            shipment_tracking=self.tracking,
        )

        self.assertEqual(second.task.id, first.task.id)
        self.assertIsNone(self.device_events.latest_location(first.task.id))

    def test_vehicle_bound_to_another_cargo_is_rejected(self) -> None:
        self.bindings.bind_cargo_to_vehicle(
            {
                "cargoId": "cargo-pending-001",
                "vehicleId": "vehicle-available-001",
            },
            WAREHOUSE_ADMIN,
            vehicles=self.vehicles,
            device_events=self.device_events,
            shipment_tracking=self.tracking,
        )

        with self.assertRaises(CargoBindingConflictError):
            self.bindings.bind_cargo_to_vehicle(
                {
                    "cargoId": "cargo-demo-001",
                    "vehicleId": "vehicle-available-001",
                },
                WAREHOUSE_ADMIN,
                vehicles=self.vehicles,
                device_events=self.device_events,
                shipment_tracking=self.tracking,
            )

    def test_warehouse_admin_must_match_cargo_scope(self) -> None:
        out_of_scope = Principal(
            "warehouse-admin-2",
            Role.WAREHOUSE_ADMIN,
            "cgf-demo",
            warehouse_ids=("warehouse-other",),
        )

        with self.assertRaises(CargoBindingError) as context:
            self.bindings.bind_cargo_to_vehicle(
                {
                    "cargoId": "cargo-pending-001",
                    "vehicleId": "vehicle-available-001",
                },
                out_of_scope,
                vehicles=self.vehicles,
                device_events=self.device_events,
                shipment_tracking=self.tracking,
            )

        self.assertEqual(context.exception.error_code, "cargo_binding_access_denied")


if __name__ == "__main__":
    unittest.main()
