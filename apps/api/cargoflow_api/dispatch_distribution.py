"""Dispatcher vehicle distribution query model for CargoFlow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.domain import (
    AlertSeverity,
    TransportTaskStatus,
    VehicleBindingStatus,
    VehicleOnlineStatus,
)


class DispatchDistributionError(Exception):
    """Raised when a dispatcher distribution query cannot be completed."""

    def __init__(self, error_code: str, message: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


class DispatchDistributionAuthorizationError(DispatchDistributionError):
    def __init__(self) -> None:
        super().__init__(
            "dispatch_distribution_access_denied",
            "Only dispatchers and system admins can read vehicle distribution.",
            HTTPStatus.FORBIDDEN,
        )


class DispatchDistributionValidationError(DispatchDistributionError):
    def __init__(self, message: str) -> None:
        super().__init__(
            "invalid_distribution_filter",
            message,
            HTTPStatus.BAD_REQUEST,
        )


@dataclass(frozen=True, slots=True)
class DistributionLocation:
    longitude: float
    latitude: float
    updated_at: datetime
    speed_kph: float | None = None

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "longitude": self.longitude,
            "latitude": self.latitude,
            "updatedAt": self.updated_at.isoformat(),
        }
        if self.speed_kph is not None:
            payload["speedKph"] = self.speed_kph
        return payload


@dataclass(frozen=True, slots=True)
class DistributionAlertSummary:
    active_count: int = 0
    highest_severity: AlertSeverity | None = None
    alert_ids: tuple[str, ...] = ()

    @property
    def has_active_alert(self) -> bool:
        return self.active_count > 0

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "activeCount": self.active_count,
            "hasActiveAlert": self.has_active_alert,
            "alertIds": list(self.alert_ids),
        }
        if self.highest_severity is not None:
            payload["highestSeverity"] = self.highest_severity.value
        return payload


@dataclass(frozen=True, slots=True)
class DistributionVehicle:
    vehicle_id: str
    vehicle_number: str
    plate_number: str
    device_id: str
    tenant_id: str
    warehouse_id: str
    dispatch_region_ids: tuple[str, ...]
    online_status: VehicleOnlineStatus
    binding_status: VehicleBindingStatus
    latest_location: DistributionLocation
    transport_status: TransportTaskStatus | None = None
    shipment_id: str | None = None
    cargo_id: str | None = None
    task_id: str | None = None
    driver_user_id: str | None = None
    alert_summary: DistributionAlertSummary = DistributionAlertSummary()

    def to_wire(self) -> dict[str, Any]:
        return {
            "vehicleId": self.vehicle_id,
            "vehicleNumber": self.vehicle_number,
            "plateNumber": self.plate_number,
            "deviceId": self.device_id,
            "warehouseId": self.warehouse_id,
            "dispatchRegionIds": list(self.dispatch_region_ids),
            "onlineStatus": self.online_status.value,
            "bindingStatus": self.binding_status.value,
            "transportStatus": (
                self.transport_status.value if self.transport_status else "idle"
            ),
            "shipmentId": self.shipment_id,
            "cargoId": self.cargo_id,
            "taskId": self.task_id,
            "driverUserId": self.driver_user_id,
            "latestLocation": self.latest_location.to_wire(),
            "alertSummary": self.alert_summary.to_wire(),
        }


class DispatchDistributionStore:
    """In-memory dispatcher vehicle map projection until persistence is wired."""

    allowed_filters = {"", "online", "in_transit", "alert"}

    def __init__(self, vehicles: tuple[DistributionVehicle, ...]) -> None:
        self._vehicles = vehicles

    @classmethod
    def demo(cls) -> "DispatchDistributionStore":
        return cls(
            (
                DistributionVehicle(
                    vehicle_id="vehicle-demo-001",
                    vehicle_number="VH-DEMO-001",
                    plate_number="CF-2026",
                    device_id="gps-demo-001",
                    tenant_id="cgf-demo",
                    warehouse_id="warehouse-shanghai",
                    dispatch_region_ids=("east-china",),
                    online_status=VehicleOnlineStatus.ONLINE,
                    binding_status=VehicleBindingStatus.BOUND,
                    transport_status=TransportTaskStatus.IN_TRANSIT,
                    shipment_id="CGF-DEMO-001",
                    cargo_id="cargo-demo-001",
                    task_id="task-demo-001",
                    driver_user_id="driver-demo",
                    latest_location=DistributionLocation(
                        longitude=121.52,
                        latitude=31.26,
                        updated_at=_parse_datetime("2026-05-13T10:12:05+00:00"),
                        speed_kph=48.5,
                    ),
                    alert_summary=DistributionAlertSummary(
                        active_count=1,
                        highest_severity=AlertSeverity.HIGH,
                        alert_ids=("alert-demo-box-001",),
                    ),
                ),
                DistributionVehicle(
                    vehicle_id="vehicle-demo-002",
                    vehicle_number="VH-DEMO-002",
                    plate_number="CF-2038",
                    device_id="gps-demo-002",
                    tenant_id="cgf-demo",
                    warehouse_id="warehouse-shanghai",
                    dispatch_region_ids=("east-china",),
                    online_status=VehicleOnlineStatus.ONLINE,
                    binding_status=VehicleBindingStatus.BOUND,
                    transport_status=TransportTaskStatus.IN_TRANSIT,
                    shipment_id="CGF-DEMO-002",
                    cargo_id="cargo-demo-002",
                    task_id="task-demo-002",
                    driver_user_id="driver-amy",
                    latest_location=DistributionLocation(
                        longitude=121.58,
                        latitude=31.31,
                        updated_at=_parse_datetime("2026-05-13T10:09:30+00:00"),
                        speed_kph=55.0,
                    ),
                ),
                DistributionVehicle(
                    vehicle_id="vehicle-demo-003",
                    vehicle_number="VH-DEMO-003",
                    plate_number="CF-2198",
                    device_id="gps-demo-003",
                    tenant_id="cgf-demo",
                    warehouse_id="warehouse-suzhou",
                    dispatch_region_ids=("east-china",),
                    online_status=VehicleOnlineStatus.DELAYED,
                    binding_status=VehicleBindingStatus.BOUND,
                    transport_status=TransportTaskStatus.LOADED,
                    shipment_id="CGF-DEMO-003",
                    cargo_id="cargo-demo-003",
                    task_id="task-demo-003",
                    driver_user_id="driver-chen",
                    latest_location=DistributionLocation(
                        longitude=121.1,
                        latitude=31.43,
                        updated_at=_parse_datetime("2026-05-13T09:42:00+00:00"),
                        speed_kph=0.0,
                    ),
                ),
                DistributionVehicle(
                    vehicle_id="vehicle-demo-004",
                    vehicle_number="VH-DEMO-004",
                    plate_number="CF-2260",
                    device_id="gps-demo-004",
                    tenant_id="cgf-demo",
                    warehouse_id="warehouse-shanghai",
                    dispatch_region_ids=("east-china",),
                    online_status=VehicleOnlineStatus.OFFLINE,
                    binding_status=VehicleBindingStatus.AVAILABLE,
                    latest_location=DistributionLocation(
                        longitude=121.39,
                        latitude=31.19,
                        updated_at=_parse_datetime("2026-05-13T08:55:00+00:00"),
                    ),
                ),
            )
        )

    def list_vehicles(
        self,
        principal: Principal,
        *,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        _require_distribution_reader(principal)
        normalized_filter = _normalize_filter(status_filter)
        scoped = [
            vehicle
            for vehicle in self._vehicles
            if _principal_can_read_vehicle(principal, vehicle)
        ]
        visible = [
            vehicle
            for vehicle in scoped
            if _matches_filter(vehicle, normalized_filter)
        ]
        return {
            "vehicles": [vehicle.to_wire() for vehicle in visible],
            "count": len(visible),
            "filters": {"status": normalized_filter},
            "summary": _summary(scoped),
        }


def _require_distribution_reader(principal: Principal) -> None:
    if principal.role not in {Role.DISPATCHER, Role.SYSTEM_ADMIN}:
        raise DispatchDistributionAuthorizationError()


def _principal_can_read_vehicle(
    principal: Principal,
    vehicle: DistributionVehicle,
) -> bool:
    if principal.tenant_id != vehicle.tenant_id:
        return False
    if principal.role is Role.SYSTEM_ADMIN:
        return True
    return principal.role is Role.DISPATCHER and bool(
        set(principal.dispatch_region_ids).intersection(vehicle.dispatch_region_ids)
    )


def _normalize_filter(status_filter: str | None) -> str:
    if status_filter is None:
        return ""
    normalized = status_filter.strip().lower().replace("-", "_")
    if normalized not in DispatchDistributionStore.allowed_filters:
        allowed = ", ".join(sorted(item or "all" for item in DispatchDistributionStore.allowed_filters))
        raise DispatchDistributionValidationError(
            f"status must be one of: {allowed}."
        )
    return normalized


def _matches_filter(vehicle: DistributionVehicle, status_filter: str) -> bool:
    if status_filter == "online":
        return vehicle.online_status is VehicleOnlineStatus.ONLINE
    if status_filter == "in_transit":
        return vehicle.transport_status is TransportTaskStatus.IN_TRANSIT
    if status_filter == "alert":
        return vehicle.alert_summary.has_active_alert
    return True


def _summary(vehicles: list[DistributionVehicle]) -> dict[str, int]:
    return {
        "total": len(vehicles),
        "online": sum(
            1 for vehicle in vehicles if vehicle.online_status is VehicleOnlineStatus.ONLINE
        ),
        "inTransit": sum(
            1
            for vehicle in vehicles
            if vehicle.transport_status is TransportTaskStatus.IN_TRANSIT
        ),
        "alerting": sum(
            1 for vehicle in vehicles if vehicle.alert_summary.has_active_alert
        ),
    }


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)
