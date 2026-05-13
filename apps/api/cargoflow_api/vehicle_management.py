"""Vehicle management rules for the current CargoFlow API skeleton."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from http import HTTPStatus
from threading import Lock
from typing import Any, Iterable, Mapping
from uuid import uuid4

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.domain import Vehicle, VehicleBindingStatus, VehicleOnlineStatus


class VehicleManagementError(Exception):
    """Raised when a vehicle management request cannot be completed."""

    def __init__(self, error_code: str, message: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


class VehicleValidationError(VehicleManagementError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_vehicle", message, HTTPStatus.BAD_REQUEST)


class VehicleAuthorizationError(VehicleManagementError):
    def __init__(self) -> None:
        super().__init__(
            "vehicle_access_denied",
            "Only warehouse admins and system admins can manage vehicles.",
            HTTPStatus.FORBIDDEN,
        )


class VehicleConflictError(VehicleManagementError):
    def __init__(self, field_name: str) -> None:
        super().__init__(
            "vehicle_conflict",
            f"{field_name} is already used by another vehicle.",
            HTTPStatus.CONFLICT,
        )


class VehicleStore:
    """In-memory vehicle repository until CargoFlow's database layer is wired."""

    def __init__(self, vehicles: Iterable[Vehicle] = ()) -> None:
        self._vehicles = {vehicle.id: vehicle for vehicle in vehicles}
        self._lock = Lock()

    @classmethod
    def demo(cls) -> "VehicleStore":
        return cls(
            (
                Vehicle(
                    id="vehicle-demo-001",
                    vehicle_number="VH-DEMO-001",
                    plate_number="CF-2026",
                    device_id="gps-demo-001",
                    driver_user_id="driver-demo",
                    online_status=VehicleOnlineStatus.ONLINE,
                    binding_status=VehicleBindingStatus.BOUND,
                    last_seen_at=_parse_datetime("2026-05-13T10:00:03+00:00"),
                    notes="Demo shipment vehicle",
                ),
            )
        )

    def list_vehicles(self, principal: Principal) -> list[Vehicle]:
        require_vehicle_manager(principal)
        with self._lock:
            return sorted(
                self._vehicles.values(),
                key=lambda vehicle: vehicle.vehicle_number,
            )

    def get_vehicle(self, vehicle_id: str, principal: Principal) -> Vehicle:
        require_vehicle_manager(principal)
        return self._vehicle_for(vehicle_id)

    def create_vehicle(self, payload: Mapping[str, Any], principal: Principal) -> Vehicle:
        require_vehicle_manager(principal)
        vehicle = vehicle_from_payload(payload)
        with self._lock:
            if vehicle.id in self._vehicles:
                raise VehicleConflictError("vehicleId")
            self._ensure_unique(vehicle)
            self._vehicles[vehicle.id] = vehicle
        return vehicle

    def update_vehicle(
        self,
        vehicle_id: str,
        payload: Mapping[str, Any],
        principal: Principal,
    ) -> Vehicle:
        require_vehicle_manager(principal)
        if not payload:
            raise VehicleValidationError("Request body must include at least one field.")

        with self._lock:
            current = self._vehicle_for_locked(vehicle_id)
            updated = replace(
                current,
                vehicle_number=_optional_text(payload, "vehicleNumber")
                or current.vehicle_number,
                plate_number=_optional_text(payload, "plateNumber")
                or current.plate_number,
                device_id=_optional_text(payload, "deviceId") or current.device_id,
                driver_user_id=_optional_nullable_text(payload, "driverUserId")
                if _has_key(payload, "driverUserId")
                else current.driver_user_id,
                online_status=_optional_online_status(payload)
                or current.online_status,
                notes=_optional_nullable_text(payload, "notes")
                if _has_key(payload, "notes")
                else current.notes,
                updated_at=utc_now(),
            )
            self._ensure_unique(updated, current_vehicle_id=vehicle_id)
            self._vehicles[vehicle_id] = updated
            return updated

    def disable_vehicle(
        self,
        vehicle_id: str,
        principal: Principal,
        *,
        reason: str | None = None,
    ) -> Vehicle:
        require_vehicle_manager(principal)
        with self._lock:
            current = self._vehicle_for_locked(vehicle_id)
            updated = replace(
                current,
                binding_status=VehicleBindingStatus.DISABLED,
                online_status=VehicleOnlineStatus.OFFLINE,
                notes=_merge_reason(current.notes, reason),
                updated_at=utc_now(),
            )
            self._vehicles[vehicle_id] = updated
            return updated

    def unbind_vehicle(
        self,
        vehicle_id: str,
        principal: Principal,
        *,
        reason: str | None = None,
    ) -> Vehicle:
        require_vehicle_manager(principal)
        with self._lock:
            current = self._vehicle_for_locked(vehicle_id)
            if current.binding_status is VehicleBindingStatus.DISABLED:
                raise VehicleValidationError("Disabled vehicles cannot be unbound.")
            updated = replace(
                current,
                binding_status=VehicleBindingStatus.AVAILABLE,
                driver_user_id=None,
                notes=_merge_reason(current.notes, reason),
                updated_at=utc_now(),
            )
            self._vehicles[vehicle_id] = updated
            return updated

    def _vehicle_for(self, vehicle_id: str) -> Vehicle:
        with self._lock:
            return self._vehicle_for_locked(vehicle_id)

    def _vehicle_for_locked(self, vehicle_id: str) -> Vehicle:
        try:
            return self._vehicles[vehicle_id]
        except KeyError as exc:
            raise VehicleManagementError(
                "vehicle_not_found",
                f"No vehicle found for {vehicle_id}.",
                HTTPStatus.NOT_FOUND,
            ) from exc

    def _ensure_unique(
        self,
        candidate: Vehicle,
        *,
        current_vehicle_id: str | None = None,
    ) -> None:
        for vehicle in self._vehicles.values():
            if current_vehicle_id is not None and vehicle.id == current_vehicle_id:
                continue
            if vehicle.vehicle_number == candidate.vehicle_number:
                raise VehicleConflictError("vehicleNumber")
            if vehicle.plate_number == candidate.plate_number:
                raise VehicleConflictError("plateNumber")
            if vehicle.device_id == candidate.device_id:
                raise VehicleConflictError("deviceId")


def require_vehicle_manager(principal: Principal) -> None:
    if principal.role not in {Role.WAREHOUSE_ADMIN, Role.SYSTEM_ADMIN}:
        raise VehicleAuthorizationError()


def vehicle_from_payload(payload: Mapping[str, Any]) -> Vehicle:
    vehicle_number = _required_text(payload, "vehicleNumber")
    plate_number = _required_text(payload, "plateNumber")
    device_id = _required_text(payload, "deviceId")
    now = utc_now()
    return Vehicle(
        id=_optional_text(payload, "vehicleId") or f"vehicle-{uuid4().hex}",
        vehicle_number=vehicle_number,
        plate_number=plate_number,
        device_id=device_id,
        driver_user_id=_optional_nullable_text(payload, "driverUserId"),
        online_status=_optional_online_status(payload) or VehicleOnlineStatus.OFFLINE,
        binding_status=_optional_binding_status(payload) or VehicleBindingStatus.AVAILABLE,
        notes=_optional_nullable_text(payload, "notes"),
        created_at=now,
        updated_at=now,
    )


def vehicle_to_wire(vehicle: Vehicle) -> dict[str, Any]:
    return {
        "vehicleId": vehicle.id,
        "vehicleNumber": vehicle.vehicle_number,
        "plateNumber": vehicle.plate_number,
        "deviceId": vehicle.device_id,
        "driverUserId": vehicle.driver_user_id,
        "onlineStatus": vehicle.online_status.value,
        "bindingStatus": vehicle.binding_status.value,
        "lastSeenAt": vehicle.last_seen_at.isoformat() if vehicle.last_seen_at else None,
        "notes": vehicle.notes,
        "createdAt": vehicle.created_at.isoformat(),
        "updatedAt": vehicle.updated_at.isoformat(),
    }


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = _value(payload, name)
    if not isinstance(value, str) or not value.strip():
        raise VehicleValidationError(f"{name} must be a non-empty string.")
    return value.strip()


def _optional_text(payload: Mapping[str, Any], name: str) -> str | None:
    value = _value(payload, name, default=None)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise VehicleValidationError(f"{name} must be a non-empty string.")
    return value.strip()


def _optional_nullable_text(payload: Mapping[str, Any], name: str) -> str | None:
    value = _value(payload, name, default=None)
    if value is None:
        return None
    if not isinstance(value, str):
        raise VehicleValidationError(f"{name} must be a string or null.")
    stripped = value.strip()
    return stripped or None


def _optional_online_status(payload: Mapping[str, Any]) -> VehicleOnlineStatus | None:
    value = _value(payload, "onlineStatus", default=None)
    if value is None:
        return None
    return _enum_from_wire(VehicleOnlineStatus, value, "onlineStatus")


def _optional_binding_status(payload: Mapping[str, Any]) -> VehicleBindingStatus | None:
    value = _value(payload, "bindingStatus", default=None)
    if value is None:
        return None
    return _enum_from_wire(VehicleBindingStatus, value, "bindingStatus")


def _enum_from_wire(enum_type: type[Any], value: Any, field_name: str) -> Any:
    if not isinstance(value, str):
        raise VehicleValidationError(f"{field_name} must be a string.")
    normalized = value.strip().lower().replace("-", "_")
    try:
        return enum_type(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise VehicleValidationError(
            f"{field_name} must be one of: {allowed}."
        ) from exc


def _value(payload: Mapping[str, Any], name: str, default: Any = ...) -> Any:
    if name in payload:
        return payload[name]
    snake_name = _camel_to_snake(name)
    if snake_name in payload:
        return payload[snake_name]
    if default is ...:
        raise VehicleValidationError(f"Missing required field: {name}.")
    return default


def _has_key(payload: Mapping[str, Any], name: str) -> bool:
    return name in payload or _camel_to_snake(name) in payload


def _merge_reason(notes: str | None, reason: str | None) -> str | None:
    if reason is None or not reason.strip():
        return notes
    clean_reason = reason.strip()
    if notes:
        return f"{notes}\nReason: {clean_reason}"
    return f"Reason: {clean_reason}"


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _camel_to_snake(value: str) -> str:
    output: list[str] = []
    for character in value:
        if character.isupper():
            output.append("_")
            output.append(character.lower())
        else:
            output.append(character)
    return "".join(output).lstrip("_")
