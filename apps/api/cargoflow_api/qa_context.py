"""Authorization-scoped business context retrieval for CargoFlow Q&A."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from cargoflow_api.access_control import Principal, Role, ShipmentScope
from cargoflow_api.domain import Alert, Cargo, TransportTask, Vehicle

BusinessContextKind = Literal["cargo", "transport_task", "vehicle", "alert"]


@dataclass(frozen=True, slots=True)
class BusinessContextScope:
    tenant_id: str
    owner_user_id: str
    driver_user_id: str
    warehouse_ids: tuple[str, ...]
    dispatch_region_ids: tuple[str, ...]

    @classmethod
    def from_shipment(cls, shipment: ShipmentScope) -> "BusinessContextScope":
        return cls(
            tenant_id=shipment.tenant_id,
            owner_user_id=shipment.owner_user_id,
            driver_user_id=shipment.driver_user_id,
            warehouse_ids=shipment.warehouse_ids,
            dispatch_region_ids=shipment.dispatch_region_ids,
        )


@dataclass(frozen=True, slots=True)
class CargoContextRecord:
    cargo: Cargo
    scope: BusinessContextScope
    shipment_id: str
    task_id: str | None = None
    vehicle_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskContextRecord:
    task: TransportTask
    scope: BusinessContextScope
    shipment_id: str
    cargo_number: str | None = None


@dataclass(frozen=True, slots=True)
class VehicleContextRecord:
    vehicle: Vehicle
    tenant_id: str
    business_scope: BusinessContextScope | None = None
    shipment_id: str | None = None
    task_id: str | None = None
    cargo_id: str | None = None
    cargo_number: str | None = None
    dispatch_region_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AlertContextRecord:
    alert: Alert
    scope: BusinessContextScope
    shipment_id: str | None = None
    cargo_number: str | None = None
    task_number: str | None = None


@dataclass(frozen=True, slots=True)
class BusinessContextRef:
    type: BusinessContextKind
    id: str
    display: str
    data: dict[str, object]

    def to_wire(self) -> dict[str, object]:
        return {
            "type": self.type,
            "id": self.id,
            "display": self.display,
            "data": self.data,
        }


class BusinessContextFilter:
    """Filters Q&A business context candidates before model retrieval."""

    def __init__(
        self,
        *,
        cargos: Iterable[CargoContextRecord] = (),
        tasks: Iterable[TaskContextRecord] = (),
        vehicles: Iterable[VehicleContextRecord] = (),
        alerts: Iterable[AlertContextRecord] = (),
    ) -> None:
        self._cargos = tuple(cargos)
        self._tasks = tuple(tasks)
        self._vehicles = tuple(vehicles)
        self._alerts = tuple(alerts)

    def authorized_refs(
        self,
        principal: Principal,
        *,
        requested_ids: Iterable[str] = (),
        requested_types: Iterable[BusinessContextKind] = (),
    ) -> tuple[BusinessContextRef, ...]:
        """Return only business refs the principal can use as Q&A context."""

        id_filter = {value for value in requested_ids if value}
        type_filter = {value for value in requested_types if value}
        refs: list[BusinessContextRef] = []

        if _include_type(type_filter, "cargo"):
            refs.extend(
                _cargo_ref(record)
                for record in self._cargos
                if _matches_cargo(record, id_filter)
                and _principal_can_read_business_scope(principal, record.scope)
            )
        if _include_type(type_filter, "transport_task"):
            refs.extend(
                _task_ref(record)
                for record in self._tasks
                if _matches_task(record, id_filter)
                and _principal_can_read_business_scope(principal, record.scope)
            )
        if _include_type(type_filter, "vehicle"):
            refs.extend(
                _vehicle_ref(record, principal)
                for record in self._vehicles
                if _matches_vehicle(record, id_filter)
                and _principal_can_read_vehicle_record(principal, record)
            )
        if _include_type(type_filter, "alert"):
            refs.extend(
                _alert_ref(record)
                for record in self._alerts
                if _matches_alert(record, id_filter)
                and _principal_can_read_business_scope(principal, record.scope)
            )

        return tuple(sorted(refs, key=lambda ref: (ref.type, ref.id)))

    def authorization_summary(
        self,
        principal: Principal,
        *,
        requested_ids: Iterable[str] = (),
        requested_types: Iterable[BusinessContextKind] = (),
    ) -> dict[str, object]:
        """Build the audit payload stored with a future Q&A record."""

        requested_id_tuple = tuple(value for value in requested_ids if value)
        requested_type_tuple = tuple(value for value in requested_types if value)
        refs = self.authorized_refs(
            principal,
            requested_ids=requested_id_tuple,
            requested_types=requested_type_tuple,
        )
        return {
            "principal": {
                "userId": principal.user_id,
                "role": principal.role.value,
                "tenantId": principal.tenant_id,
            },
            "requestedIds": list(requested_id_tuple),
            "requestedTypes": list(requested_type_tuple),
            "authorizedRefCount": len(refs),
            "authorizedRefs": [
                {
                    "type": ref.type,
                    "id": ref.id,
                    "display": ref.display,
                }
                for ref in refs
            ],
        }


def _principal_can_read_business_scope(
    principal: Principal,
    scope: BusinessContextScope,
) -> bool:
    if principal.tenant_id != scope.tenant_id:
        return False
    if principal.role is Role.SYSTEM_ADMIN:
        return True
    if principal.role is Role.CARGO_OWNER:
        return principal.user_id == scope.owner_user_id
    if principal.role is Role.DRIVER:
        return principal.user_id == scope.driver_user_id
    if principal.role is Role.WAREHOUSE_ADMIN:
        return _intersects(principal.warehouse_ids, scope.warehouse_ids)
    if principal.role is Role.DISPATCHER:
        return _intersects(principal.dispatch_region_ids, scope.dispatch_region_ids)
    return False


def _principal_can_read_vehicle_record(
    principal: Principal,
    record: VehicleContextRecord,
) -> bool:
    vehicle = record.vehicle
    if principal.tenant_id != record.tenant_id:
        return False
    if principal.role is Role.SYSTEM_ADMIN:
        return True
    if record.business_scope is not None and principal.role in {
        Role.CARGO_OWNER,
        Role.DRIVER,
        Role.DISPATCHER,
    }:
        return _principal_can_read_business_scope(principal, record.business_scope)
    if principal.role is Role.DRIVER:
        return principal.user_id == vehicle.driver_user_id
    if principal.role is Role.WAREHOUSE_ADMIN:
        return vehicle.warehouse_id in principal.warehouse_ids
    if principal.role is Role.DISPATCHER:
        return _intersects(principal.dispatch_region_ids, record.dispatch_region_ids)
    return False


def _cargo_ref(record: CargoContextRecord) -> BusinessContextRef:
    cargo = record.cargo
    return BusinessContextRef(
        type="cargo",
        id=cargo.id,
        display=f"Cargo {cargo.cargo_number}",
        data={
            "cargoId": cargo.id,
            "cargoNumber": cargo.cargo_number,
            "shipmentId": record.shipment_id,
            "taskId": record.task_id,
            "vehicleId": record.vehicle_id,
            "name": cargo.name,
            "origin": cargo.origin,
            "destination": cargo.destination,
            "status": cargo.current_status.value,
        },
    )


def _task_ref(record: TaskContextRecord) -> BusinessContextRef:
    task = record.task
    return BusinessContextRef(
        type="transport_task",
        id=task.id,
        display=f"Transport task {task.task_number}",
        data={
            "taskId": task.id,
            "taskNumber": task.task_number,
            "shipmentId": record.shipment_id,
            "cargoId": task.cargo_id,
            "cargoNumber": record.cargo_number,
            "vehicleId": task.vehicle_id,
            "origin": task.origin,
            "destination": task.destination,
            "status": task.status.value,
        },
    )


def _vehicle_ref(
    record: VehicleContextRecord,
    principal: Principal,
) -> BusinessContextRef:
    vehicle = record.vehicle
    data: dict[str, object] = {
        "vehicleId": vehicle.id,
        "warehouseId": vehicle.warehouse_id,
        "vehicleNumber": vehicle.vehicle_number,
        "plateNumber": vehicle.plate_number,
        "deviceId": vehicle.device_id,
        "onlineStatus": vehicle.online_status.value,
        "bindingStatus": vehicle.binding_status.value,
    }
    if principal.role in {Role.DRIVER, Role.WAREHOUSE_ADMIN, Role.SYSTEM_ADMIN}:
        data["driverUserId"] = vehicle.driver_user_id
    return BusinessContextRef(
        type="vehicle",
        id=vehicle.id,
        display=f"Vehicle {vehicle.vehicle_number}",
        data=data,
    )


def _alert_ref(record: AlertContextRecord) -> BusinessContextRef:
    alert = record.alert
    return BusinessContextRef(
        type="alert",
        id=alert.id,
        display=f"Alert {alert.alert_number}",
        data={
            "alertId": alert.id,
            "alertNumber": alert.alert_number,
            "taskId": alert.task_id,
            "cargoId": alert.cargo_id,
            "vehicleId": alert.vehicle_id,
            "alertType": alert.alert_type.value,
            "severity": alert.severity.value,
            "status": alert.status.value,
            "triggeredAt": alert.triggered_at.isoformat(),
        },
    )


def _matches_cargo(record: CargoContextRecord, id_filter: set[str]) -> bool:
    cargo = record.cargo
    values = {
        cargo.id,
        cargo.cargo_number,
        record.shipment_id,
        record.task_id or "",
        record.vehicle_id or "",
    }
    return _matches_any(values, id_filter)


def _matches_task(record: TaskContextRecord, id_filter: set[str]) -> bool:
    task = record.task
    return _matches_any(
        {
            task.id,
            task.task_number,
            task.cargo_id,
            task.vehicle_id,
            record.shipment_id,
            record.cargo_number or "",
        },
        id_filter,
    )


def _matches_vehicle(record: VehicleContextRecord, id_filter: set[str]) -> bool:
    vehicle = record.vehicle
    return _matches_any(
        {
            vehicle.id,
            vehicle.vehicle_number,
            vehicle.plate_number,
            vehicle.device_id,
            record.shipment_id or "",
            record.task_id or "",
            record.cargo_id or "",
            record.cargo_number or "",
        },
        id_filter,
    )


def _matches_alert(record: AlertContextRecord, id_filter: set[str]) -> bool:
    alert = record.alert
    return _matches_any(
        {
            alert.id,
            alert.alert_number,
            alert.task_id,
            alert.cargo_id,
            alert.vehicle_id,
            record.shipment_id or "",
            record.cargo_number or "",
            record.task_number or "",
        },
        id_filter,
    )


def _matches_any(values: set[str], id_filter: set[str]) -> bool:
    return not id_filter or bool(values.intersection(id_filter))


def _include_type(
    requested_types: set[BusinessContextKind],
    candidate: BusinessContextKind,
) -> bool:
    return not requested_types or candidate in requested_types


def _intersects(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return bool(set(left).intersection(right))
