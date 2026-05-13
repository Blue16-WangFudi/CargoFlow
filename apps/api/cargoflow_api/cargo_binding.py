"""Cargo-to-vehicle binding workflow for the current API skeleton."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from http import HTTPStatus
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.domain import (
    Cargo,
    TransportTask,
    TransportTaskStatus,
    VehicleBindingStatus,
)
from cargoflow_api.domain.models import utc_now
from cargoflow_api.eta import Destination
from cargoflow_api.location_ingest import DeviceEventStore, DeviceTaskBinding
from cargoflow_api.shipment_tracking import (
    ShipmentTrackingRecord,
    ShipmentTrackingStore,
    VehicleSummary,
)
from cargoflow_api.vehicle_management import (
    VehicleStore,
    vehicle_to_wire,
)


class CargoBindingError(Exception):
    """Raised when a cargo/vehicle binding request cannot be completed."""

    def __init__(self, error_code: str, message: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


class CargoBindingValidationError(CargoBindingError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_cargo_binding", message, HTTPStatus.BAD_REQUEST)


class CargoBindingAuthorizationError(CargoBindingError):
    def __init__(self, message: str) -> None:
        super().__init__("cargo_binding_access_denied", message, HTTPStatus.FORBIDDEN)


class CargoBindingConflictError(CargoBindingError):
    def __init__(self, message: str) -> None:
        super().__init__("cargo_binding_conflict", message, HTTPStatus.CONFLICT)


@dataclass(frozen=True, slots=True)
class CargoBindingCargo:
    cargo: Cargo
    tenant_id: str
    warehouse_ids: tuple[str, ...]
    dispatch_region_ids: tuple[str, ...]
    shipment_id: str
    planned_start: Destination | None = None
    destination_point: Destination | None = None


@dataclass(frozen=True, slots=True)
class CargoVehicleBindingResult:
    cargo: Cargo
    task: TransportTask
    vehicle: dict[str, Any]
    shipment_id: str
    created: bool

    def to_wire(self) -> dict[str, Any]:
        return {
            "binding": {
                "shipmentId": self.shipment_id,
                "cargoId": self.cargo.id,
                "cargoNumber": self.cargo.cargo_number,
                "taskId": self.task.id,
                "taskNumber": self.task.task_number,
                "transportStatus": self.task.status.value,
                "vehicle": self.vehicle,
                "created": self.created,
            }
        }


class CargoBindingStore:
    """In-memory cargo binding state until persistence is connected."""

    def __init__(
        self,
        cargos: tuple[CargoBindingCargo, ...],
        tasks: tuple[TransportTask, ...] = (),
    ) -> None:
        self._cargos = {record.cargo.id: record for record in cargos}
        self._tasks = {task.id: task for task in tasks}
        self._active_task_by_cargo: dict[str, str] = {}
        self._active_task_by_vehicle: dict[str, str] = {}
        for task in tasks:
            if not task.is_terminal:
                self._active_task_by_cargo[task.cargo_id] = task.id
                self._active_task_by_vehicle[task.vehicle_id] = task.id
        self._lock = Lock()

    @classmethod
    def demo(cls) -> "CargoBindingStore":
        demo_cargo = Cargo(
            id="cargo-demo-001",
            cargo_number="CGF-DEMO-001",
            owner_user_id="owner-acme",
            name="Temperature controlled cargo",
            origin="Shanghai Pudong Warehouse",
            destination="Shanghai Waigaoqiao Logistics Park",
            current_status=TransportTaskStatus.IN_TRANSIT,
        )
        pending_cargo = Cargo(
            id="cargo-pending-001",
            cargo_number="CGF-PENDING-001",
            owner_user_id="owner-beta",
            name="Awaiting vehicle assignment",
            origin="Shanghai Pudong Warehouse",
            destination="Suzhou Distribution Center",
        )
        demo_task = TransportTask(
            id="task-demo-001",
            task_number="TASK-DEMO-001",
            cargo_id=demo_cargo.id,
            vehicle_id="vehicle-demo-001",
            driver_user_id="driver-demo",
            origin=demo_cargo.origin,
            destination=demo_cargo.destination,
            status=TransportTaskStatus.IN_TRANSIT,
        )
        return cls(
            (
                CargoBindingCargo(
                    cargo=demo_cargo,
                    tenant_id="cgf-demo",
                    warehouse_ids=("warehouse-shanghai",),
                    dispatch_region_ids=("east-china",),
                    shipment_id=demo_cargo.cargo_number,
                    planned_start=Destination(
                        name=demo_cargo.origin,
                        longitude=121.4737,
                        latitude=31.2304,
                    ),
                    destination_point=Destination(
                        name=demo_cargo.destination,
                        longitude=121.5956,
                        latitude=31.3479,
                    ),
                ),
                CargoBindingCargo(
                    cargo=pending_cargo,
                    tenant_id="cgf-demo",
                    warehouse_ids=("warehouse-shanghai",),
                    dispatch_region_ids=("east-china",),
                    shipment_id=pending_cargo.cargo_number,
                    planned_start=Destination(
                        name=pending_cargo.origin,
                        longitude=121.4737,
                        latitude=31.2304,
                    ),
                    destination_point=Destination(
                        name=pending_cargo.destination,
                        longitude=120.5853,
                        latitude=31.2989,
                    ),
                ),
            ),
            (demo_task,),
        )

    def bind_cargo_to_vehicle(
        self,
        payload: Mapping[str, Any],
        principal: Principal,
        *,
        vehicles: VehicleStore,
        device_events: DeviceEventStore,
        shipment_tracking: ShipmentTrackingStore,
    ) -> CargoVehicleBindingResult:
        cargo_id = _required_text(payload, "cargoId")
        vehicle_id = _required_text(payload, "vehicleId")
        driver_user_id = _optional_text(payload, "driverUserId")

        with self._lock:
            cargo_record = self._cargo_for(cargo_id)
            _require_binding_scope(principal, cargo_record)
            vehicle = vehicles.get_vehicle(vehicle_id, principal)
            if vehicle.binding_status is VehicleBindingStatus.DISABLED:
                raise CargoBindingValidationError("Disabled vehicles cannot be bound.")

            active_task_id = self._active_task_by_vehicle.get(vehicle.id)
            if active_task_id is not None:
                active_task = self._tasks[active_task_id]
                if active_task.cargo_id != cargo_record.cargo.id:
                    raise CargoBindingConflictError(
                        "Vehicle is already bound to another active cargo."
                    )

            task_id = self._active_task_by_cargo.get(cargo_record.cargo.id)
            now = _utc_now()
            effective_driver_id = driver_user_id or vehicle.driver_user_id
            if not effective_driver_id:
                raise CargoBindingValidationError(
                    "driverUserId is required when the vehicle has no assigned driver."
                )

            created = task_id is None
            if created:
                task = TransportTask(
                    id=_optional_text(payload, "taskId") or f"task-{uuid4().hex}",
                    task_number=_optional_text(payload, "taskNumber")
                    or f"TASK-{cargo_record.cargo.cargo_number}",
                    cargo_id=cargo_record.cargo.id,
                    vehicle_id=vehicle.id,
                    driver_user_id=effective_driver_id,
                    origin=cargo_record.cargo.origin,
                    destination=cargo_record.cargo.destination,
                    status=TransportTaskStatus.BOUND,
                    planned_departure_at=cargo_record.cargo.planned_departure_at,
                    planned_arrival_at=cargo_record.cargo.planned_arrival_at,
                    created_at=now,
                    updated_at=now,
                )
            else:
                task = self._tasks[task_id]
                previous_vehicle_id = task.vehicle_id
                task = replace(
                    task,
                    vehicle_id=vehicle.id,
                    driver_user_id=effective_driver_id,
                    status=TransportTaskStatus.BOUND
                    if task.status is TransportTaskStatus.PENDING_BINDING
                    else task.status,
                    updated_at=now,
                )
                if previous_vehicle_id != vehicle.id:
                    self._active_task_by_vehicle.pop(previous_vehicle_id, None)
                    previous_vehicle = vehicles.get_vehicle(previous_vehicle_id, principal)
                    device_events.unbind_device(previous_vehicle.device_id, task_id=task.id)
                    device_events.clear_latest_location(task.id)
                    vehicles.unbind_vehicle(
                        previous_vehicle_id,
                        principal,
                        reason=f"Rebound cargo {cargo_record.cargo.cargo_number}",
                    )

            bound_vehicle = vehicles.bind_vehicle(
                vehicle.id,
                principal,
                driver_user_id=effective_driver_id,
            )
            updated_cargo = replace(
                cargo_record.cargo,
                current_status=task.status,
                updated_at=now,
            )
            self._cargos[cargo_record.cargo.id] = replace(
                cargo_record,
                cargo=updated_cargo,
                shipment_id=_optional_text(payload, "shipmentId")
                or cargo_record.shipment_id,
            )
            self._tasks[task.id] = task
            self._active_task_by_cargo[task.cargo_id] = task.id
            self._active_task_by_vehicle[task.vehicle_id] = task.id

            device_events.bind_device_to_task(
                DeviceTaskBinding(
                    device_id=bound_vehicle.device_id,
                    task_id=task.id,
                    vehicle_id=bound_vehicle.id,
                )
            )
            refreshed_record = self._cargos[cargo_record.cargo.id]
            shipment_tracking.upsert_record(
                ShipmentTrackingRecord(
                    shipment_id=refreshed_record.shipment_id,
                    cargo_id=updated_cargo.id,
                    task_id=task.id,
                    tenant_id=refreshed_record.tenant_id,
                    owner_user_id=updated_cargo.owner_user_id,
                    driver_user_id=task.driver_user_id,
                    warehouse_ids=refreshed_record.warehouse_ids,
                    dispatch_region_ids=refreshed_record.dispatch_region_ids,
                    transport_status=task.status,
                    vehicle=VehicleSummary(
                        vehicle_id=bound_vehicle.id,
                        vehicle_number=bound_vehicle.vehicle_number,
                        plate_number=bound_vehicle.plate_number,
                        device_id=bound_vehicle.device_id,
                        driver_user_id=task.driver_user_id,
                    ),
                    destination=refreshed_record.destination_point,
                    planned_start=refreshed_record.planned_start,
                ),
                aliases=(updated_cargo.id, updated_cargo.cargo_number),
            )

            return CargoVehicleBindingResult(
                cargo=updated_cargo,
                task=task,
                vehicle=vehicle_to_wire(bound_vehicle),
                shipment_id=refreshed_record.shipment_id,
                created=created,
            )

    def _cargo_for(self, cargo_id: str) -> CargoBindingCargo:
        try:
            return self._cargos[cargo_id]
        except KeyError as exc:
            raise CargoBindingValidationError(f"No cargo found for {cargo_id}.") from exc


def _require_binding_scope(
    principal: Principal,
    cargo_record: CargoBindingCargo,
) -> None:
    if principal.tenant_id != cargo_record.tenant_id:
        raise CargoBindingAuthorizationError(
            "Principal is outside the cargo tenant scope."
        )
    if principal.role is Role.SYSTEM_ADMIN:
        return
    if principal.role is Role.WAREHOUSE_ADMIN and set(
        principal.warehouse_ids
    ).intersection(cargo_record.warehouse_ids):
        return
    raise CargoBindingAuthorizationError(
        "Only scoped warehouse admins and system admins can bind cargo to vehicles."
    )


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = _value(payload, name)
    if not isinstance(value, str) or not value.strip():
        raise CargoBindingValidationError(f"{name} must be a non-empty string.")
    return value.strip()


def _optional_text(payload: Mapping[str, Any], name: str) -> str | None:
    value = _value(payload, name, default=None)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CargoBindingValidationError(f"{name} must be a non-empty string.")
    return value.strip()


def _value(payload: Mapping[str, Any], name: str, default: Any = ...) -> Any:
    if name in payload:
        return payload[name]
    snake_name = _camel_to_snake(name)
    if snake_name in payload:
        return payload[snake_name]
    if default is ...:
        raise CargoBindingValidationError(f"Missing required field: {name}.")
    return default


def _camel_to_snake(value: str) -> str:
    output: list[str] = []
    for character in value:
        if character.isupper():
            output.append("_")
            output.append(character.lower())
        else:
            output.append(character)
    return "".join(output).lstrip("_")


def _utc_now() -> datetime:
    return utc_now()
