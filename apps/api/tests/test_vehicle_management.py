from __future__ import annotations

import unittest

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.domain import Vehicle, VehicleBindingStatus, VehicleOnlineStatus
from cargoflow_api.vehicle_management import (
    VehicleAuthorizationError,
    VehicleConflictError,
    VehicleScopeError,
    VehicleStore,
    VehicleValidationError,
    vehicle_from_payload,
    vehicle_to_wire,
)


WAREHOUSE_ADMIN = Principal(
    "warehouse-admin-1",
    Role.WAREHOUSE_ADMIN,
    "tenant-1",
    warehouse_ids=("warehouse-1",),
)


class VehiclePayloadTests(unittest.TestCase):
    def test_vehicle_from_payload_defaults_statuses(self) -> None:
        vehicle = vehicle_from_payload(
            {
                "vehicleId": "vehicle-1",
                "warehouseId": "warehouse-1",
                "vehicleNumber": "VH-001",
                "plateNumber": "SH-A12345",
                "deviceId": "gps-001",
            }
        )

        self.assertEqual(vehicle.id, "vehicle-1")
        self.assertEqual(vehicle.warehouse_id, "warehouse-1")
        self.assertEqual(vehicle.online_status, VehicleOnlineStatus.OFFLINE)
        self.assertEqual(vehicle.binding_status, VehicleBindingStatus.AVAILABLE)

    def test_vehicle_from_payload_defaults_warehouse_for_single_scope_admin(self) -> None:
        vehicle = vehicle_from_payload(
            {
                "vehicleId": "vehicle-1",
                "vehicleNumber": "VH-001",
                "plateNumber": "SH-A12345",
                "deviceId": "gps-001",
            },
            principal=WAREHOUSE_ADMIN,
        )

        self.assertEqual(vehicle.warehouse_id, "warehouse-1")

    def test_vehicle_from_payload_rejects_missing_unique_keys(self) -> None:
        with self.assertRaises(VehicleValidationError):
            vehicle_from_payload(
                {
                    "warehouseId": "warehouse-1",
                    "vehicleNumber": "VH-001",
                    "deviceId": "gps-001",
                }
            )

    def test_vehicle_to_wire_uses_api_field_names(self) -> None:
        vehicle = Vehicle(
            id="vehicle-1",
            warehouse_id="warehouse-1",
            vehicle_number="VH-001",
            plate_number="SH-A12345",
            device_id="gps-001",
        )

        payload = vehicle_to_wire(vehicle)

        self.assertEqual(payload["vehicleId"], "vehicle-1")
        self.assertEqual(payload["warehouseId"], "warehouse-1")
        self.assertEqual(payload["vehicleNumber"], "VH-001")
        self.assertEqual(payload["bindingStatus"], "available")


class VehicleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = VehicleStore(
            (
                Vehicle(
                    id="vehicle-1",
                    warehouse_id="warehouse-1",
                    vehicle_number="VH-001",
                    plate_number="SH-A12345",
                    device_id="gps-001",
                    driver_user_id="driver-1",
                    online_status=VehicleOnlineStatus.ONLINE,
                    binding_status=VehicleBindingStatus.BOUND,
                ),
            )
        )

    def test_warehouse_admin_can_create_and_list_vehicle(self) -> None:
        created = self.store.create_vehicle(
            {
                "vehicleId": "vehicle-2",
                "warehouseId": "warehouse-1",
                "vehicleNumber": "VH-002",
                "plateNumber": "SH-B12345",
                "deviceId": "gps-002",
            },
            WAREHOUSE_ADMIN,
        )

        vehicles = self.store.list_vehicles(WAREHOUSE_ADMIN)

        self.assertEqual(created.id, "vehicle-2")
        self.assertEqual([vehicle.id for vehicle in vehicles], ["vehicle-1", "vehicle-2"])

    def test_create_rejects_duplicate_plate_number(self) -> None:
        with self.assertRaises(VehicleConflictError):
            self.store.create_vehicle(
                {
                    "vehicleId": "vehicle-2",
                    "warehouseId": "warehouse-1",
                    "vehicleNumber": "VH-002",
                    "plateNumber": "SH-A12345",
                    "deviceId": "gps-002",
                },
                WAREHOUSE_ADMIN,
            )

    def test_create_rejects_duplicate_vehicle_id(self) -> None:
        with self.assertRaises(VehicleConflictError):
            self.store.create_vehicle(
                {
                    "vehicleId": "vehicle-1",
                    "warehouseId": "warehouse-1",
                    "vehicleNumber": "VH-002",
                    "plateNumber": "SH-B12345",
                    "deviceId": "gps-002",
                },
                WAREHOUSE_ADMIN,
            )

    def test_update_rejects_duplicate_device_id(self) -> None:
        self.store.create_vehicle(
            {
                "vehicleId": "vehicle-2",
                "warehouseId": "warehouse-1",
                "vehicleNumber": "VH-002",
                "plateNumber": "SH-B12345",
                "deviceId": "gps-002",
            },
            WAREHOUSE_ADMIN,
        )

        with self.assertRaises(VehicleConflictError):
            self.store.update_vehicle(
                "vehicle-2",
                {"deviceId": "gps-001"},
                WAREHOUSE_ADMIN,
            )

    def test_disable_vehicle_marks_it_unavailable_and_offline(self) -> None:
        vehicle = self.store.disable_vehicle(
            "vehicle-1",
            WAREHOUSE_ADMIN,
            reason="maintenance",
        )

        self.assertEqual(vehicle.binding_status, VehicleBindingStatus.DISABLED)
        self.assertEqual(vehicle.online_status, VehicleOnlineStatus.OFFLINE)
        self.assertIn("maintenance", vehicle.notes or "")

    def test_unbind_vehicle_clears_driver_and_makes_available(self) -> None:
        vehicle = self.store.unbind_vehicle(
            "vehicle-1",
            WAREHOUSE_ADMIN,
            reason="shipment completed",
        )

        self.assertEqual(vehicle.binding_status, VehicleBindingStatus.AVAILABLE)
        self.assertIsNone(vehicle.driver_user_id)
        self.assertIn("shipment completed", vehicle.notes or "")

    def test_cargo_owner_cannot_manage_vehicles(self) -> None:
        owner = Principal("owner-1", Role.CARGO_OWNER, "tenant-1")

        with self.assertRaises(VehicleAuthorizationError):
            self.store.list_vehicles(owner)

    def test_warehouse_admin_only_lists_scoped_vehicles(self) -> None:
        self.store.create_vehicle(
            {
                "vehicleId": "vehicle-2",
                "warehouseId": "warehouse-2",
                "vehicleNumber": "VH-002",
                "plateNumber": "SH-B12345",
                "deviceId": "gps-002",
            },
            Principal("system-admin-1", Role.SYSTEM_ADMIN, "tenant-1"),
        )

        vehicles = self.store.list_vehicles(WAREHOUSE_ADMIN)

        self.assertEqual([vehicle.id for vehicle in vehicles], ["vehicle-1"])

    def test_warehouse_admin_cannot_update_out_of_scope_vehicle(self) -> None:
        self.store.create_vehicle(
            {
                "vehicleId": "vehicle-2",
                "warehouseId": "warehouse-2",
                "vehicleNumber": "VH-002",
                "plateNumber": "SH-B12345",
                "deviceId": "gps-002",
            },
            Principal("system-admin-1", Role.SYSTEM_ADMIN, "tenant-1"),
        )

        with self.assertRaises(VehicleScopeError):
            self.store.update_vehicle(
                "vehicle-2",
                {"plateNumber": "SH-B54321"},
                WAREHOUSE_ADMIN,
            )


if __name__ == "__main__":
    unittest.main()
