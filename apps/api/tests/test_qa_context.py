from __future__ import annotations

import unittest
from datetime import UTC, datetime

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.domain import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AlertType,
    Cargo,
    TransportTask,
    TransportTaskStatus,
    Vehicle,
    VehicleBindingStatus,
    VehicleOnlineStatus,
)
from cargoflow_api.qa_context import (
    AlertContextRecord,
    BusinessContextFilter,
    BusinessContextScope,
    CargoContextRecord,
    TaskContextRecord,
    VehicleContextRecord,
)


class BusinessContextFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_scope = BusinessContextScope(
            tenant_id="tenant-1",
            owner_user_id="owner-1",
            driver_user_id="driver-1",
            warehouse_ids=("warehouse-1",),
            dispatch_region_ids=("region-1",),
        )
        self.other_scope = BusinessContextScope(
            tenant_id="tenant-1",
            owner_user_id="owner-2",
            driver_user_id="driver-2",
            warehouse_ids=("warehouse-2",),
            dispatch_region_ids=("region-2",),
        )
        self.cross_tenant_scope = BusinessContextScope(
            tenant_id="tenant-2",
            owner_user_id="owner-1",
            driver_user_id="driver-1",
            warehouse_ids=("warehouse-1",),
            dispatch_region_ids=("region-1",),
        )
        self.filter = BusinessContextFilter(
            cargos=(
                CargoContextRecord(
                    cargo=_cargo("cargo-1", "CGF-001", "owner-1"),
                    scope=self.owner_scope,
                    shipment_id="SHIP-001",
                    task_id="task-1",
                    vehicle_id="vehicle-1",
                ),
                CargoContextRecord(
                    cargo=_cargo("cargo-2", "CGF-002", "owner-2"),
                    scope=self.other_scope,
                    shipment_id="SHIP-002",
                    task_id="task-2",
                    vehicle_id="vehicle-2",
                ),
                CargoContextRecord(
                    cargo=_cargo("cargo-cross-tenant", "CGF-003", "owner-1"),
                    scope=self.cross_tenant_scope,
                    shipment_id="SHIP-003",
                ),
            ),
            tasks=(
                TaskContextRecord(
                    task=_task("task-1", "TASK-001", "cargo-1", "vehicle-1", "driver-1"),
                    scope=self.owner_scope,
                    shipment_id="SHIP-001",
                    cargo_number="CGF-001",
                ),
                TaskContextRecord(
                    task=_task("task-2", "TASK-002", "cargo-2", "vehicle-2", "driver-2"),
                    scope=self.other_scope,
                    shipment_id="SHIP-002",
                    cargo_number="CGF-002",
                ),
            ),
            vehicles=(
                VehicleContextRecord(
                    vehicle=_vehicle("vehicle-1", "VH-001", "warehouse-1", "driver-1"),
                    tenant_id="tenant-1",
                    business_scope=self.owner_scope,
                    shipment_id="SHIP-001",
                    task_id="task-1",
                    cargo_id="cargo-1",
                    cargo_number="CGF-001",
                    dispatch_region_ids=("region-1",),
                ),
                VehicleContextRecord(
                    vehicle=_vehicle("vehicle-2", "VH-002", "warehouse-2", "driver-2"),
                    tenant_id="tenant-1",
                    business_scope=self.other_scope,
                    shipment_id="SHIP-002",
                    task_id="task-2",
                    cargo_id="cargo-2",
                    cargo_number="CGF-002",
                    dispatch_region_ids=("region-2",),
                ),
            ),
            alerts=(
                AlertContextRecord(
                    alert=_alert("alert-1", "task-1"),
                    scope=self.owner_scope,
                    shipment_id="SHIP-001",
                    cargo_number="CGF-001",
                    task_number="TASK-001",
                ),
                AlertContextRecord(
                    alert=_alert("alert-2", "task-2"),
                    scope=self.other_scope,
                    shipment_id="SHIP-002",
                    cargo_number="CGF-002",
                    task_number="TASK-002",
                ),
            ),
        )

    def test_cargo_owner_only_receives_owned_business_context(self) -> None:
        principal = Principal("owner-1", Role.CARGO_OWNER, "tenant-1")

        refs = self.filter.authorized_refs(principal)

        self.assertEqual(
            {(ref.type, ref.id) for ref in refs},
            {
                ("alert", "alert-1"),
                ("cargo", "cargo-1"),
                ("transport_task", "task-1"),
                ("vehicle", "vehicle-1"),
            },
        )
        for ref in refs:
            self.assertNotIn("ownerUserId", ref.data)
            self.assertNotIn("driverUserId", ref.data)

    def test_cargo_owner_gets_no_context_for_another_owner_requested_id(self) -> None:
        principal = Principal("owner-1", Role.CARGO_OWNER, "tenant-1")

        refs = self.filter.authorized_refs(principal, requested_ids=("cargo-2",))

        self.assertEqual(refs, ())

    def test_driver_receives_own_task_vehicle_and_alert_context(self) -> None:
        principal = Principal("driver-1", Role.DRIVER, "tenant-1")

        refs = self.filter.authorized_refs(principal)

        self.assertEqual(
            {(ref.type, ref.id) for ref in refs},
            {
                ("alert", "alert-1"),
                ("cargo", "cargo-1"),
                ("transport_task", "task-1"),
                ("vehicle", "vehicle-1"),
            },
        )
        vehicle = next(ref for ref in refs if ref.type == "vehicle")
        self.assertEqual(vehicle.data["driverUserId"], "driver-1")

    def test_warehouse_admin_is_limited_to_warehouse_scope(self) -> None:
        principal = Principal(
            "warehouse-admin-1",
            Role.WAREHOUSE_ADMIN,
            "tenant-1",
            warehouse_ids=("warehouse-1",),
        )

        refs = self.filter.authorized_refs(principal)

        self.assertEqual(
            {(ref.type, ref.id) for ref in refs},
            {
                ("alert", "alert-1"),
                ("cargo", "cargo-1"),
                ("transport_task", "task-1"),
                ("vehicle", "vehicle-1"),
            },
        )

    def test_dispatcher_is_limited_to_dispatch_region_scope(self) -> None:
        principal = Principal(
            "dispatcher-1",
            Role.DISPATCHER,
            "tenant-1",
            dispatch_region_ids=("region-1",),
        )

        refs = self.filter.authorized_refs(principal)

        self.assertEqual(
            {(ref.type, ref.id) for ref in refs},
            {
                ("alert", "alert-1"),
                ("cargo", "cargo-1"),
                ("transport_task", "task-1"),
                ("vehicle", "vehicle-1"),
            },
        )
        vehicle = next(ref for ref in refs if ref.type == "vehicle")
        self.assertNotIn("driverUserId", vehicle.data)

    def test_system_admin_can_filter_to_requested_types_and_ids(self) -> None:
        principal = Principal("admin-1", Role.SYSTEM_ADMIN, "tenant-1")

        refs = self.filter.authorized_refs(
            principal,
            requested_types=("alert", "transport_task"),
            requested_ids=("task-2",),
        )

        self.assertEqual(
            [(ref.type, ref.id) for ref in refs],
            [("alert", "alert-2"), ("transport_task", "task-2")],
        )

    def test_cross_tenant_context_is_never_returned(self) -> None:
        principal = Principal("admin-1", Role.SYSTEM_ADMIN, "tenant-1")

        refs = self.filter.authorized_refs(principal, requested_ids=("cargo-cross-tenant",))

        self.assertEqual(refs, ())

    def test_authorization_summary_is_auditable_without_context_payloads(self) -> None:
        principal = Principal("owner-1", Role.CARGO_OWNER, "tenant-1")

        summary = self.filter.authorization_summary(
            principal,
            requested_ids=("SHIP-001", "SHIP-002"),
        )

        self.assertEqual(summary["principal"]["role"], "cargo_owner")
        self.assertEqual(summary["requestedIds"], ["SHIP-001", "SHIP-002"])
        self.assertEqual(summary["authorizedRefCount"], 4)
        authorized_refs = summary["authorizedRefs"]
        self.assertNotIn("data", authorized_refs[0])


def _cargo(cargo_id: str, cargo_number: str, owner_user_id: str) -> Cargo:
    return Cargo(
        id=cargo_id,
        cargo_number=cargo_number,
        owner_user_id=owner_user_id,
        name=f"Cargo {cargo_number}",
        origin="Shanghai",
        destination="Suzhou",
        current_status=TransportTaskStatus.IN_TRANSIT,
    )


def _task(
    task_id: str,
    task_number: str,
    cargo_id: str,
    vehicle_id: str,
    driver_user_id: str,
) -> TransportTask:
    return TransportTask(
        id=task_id,
        task_number=task_number,
        cargo_id=cargo_id,
        vehicle_id=vehicle_id,
        driver_user_id=driver_user_id,
        origin="Shanghai",
        destination="Suzhou",
        status=TransportTaskStatus.IN_TRANSIT,
    )


def _vehicle(
    vehicle_id: str,
    vehicle_number: str,
    warehouse_id: str,
    driver_user_id: str,
) -> Vehicle:
    return Vehicle(
        id=vehicle_id,
        warehouse_id=warehouse_id,
        vehicle_number=vehicle_number,
        plate_number=f"PLATE-{vehicle_number}",
        device_id=f"gps-{vehicle_id}",
        driver_user_id=driver_user_id,
        online_status=VehicleOnlineStatus.ONLINE,
        binding_status=VehicleBindingStatus.BOUND,
    )


def _alert(alert_id: str, task_id: str) -> Alert:
    suffix = alert_id.rsplit("-", 1)[-1].upper()
    return Alert(
        id=alert_id,
        alert_number=f"ALR-{suffix}",
        task_id=task_id,
        cargo_id=f"cargo-{suffix.lower()}",
        vehicle_id=f"vehicle-{suffix.lower()}",
        alert_type=AlertType.ROUTE_DEVIATION,
        severity=AlertSeverity.MEDIUM,
        status=AlertStatus.PENDING,
        triggered_at=datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
    )


if __name__ == "__main__":
    unittest.main()
