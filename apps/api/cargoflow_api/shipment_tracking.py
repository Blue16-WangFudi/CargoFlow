"""Shipment latest-location query model for the current API skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

from cargoflow_api.access_control import Principal, ShipmentScope, require_shipment_access
from cargoflow_api.domain import TransportTaskStatus
from cargoflow_api.location_ingest import DeviceEventStore, LatestLocationSnapshot


class ShipmentTrackingError(Exception):
    """Raised when a shipment tracking query cannot be completed."""

    def __init__(self, error_code: str, message: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class VehicleSummary:
    vehicle_id: str
    vehicle_number: str
    plate_number: str
    device_id: str
    driver_user_id: str

    def to_wire(self) -> dict[str, str]:
        return {
            "vehicleId": self.vehicle_id,
            "vehicleNumber": self.vehicle_number,
            "plateNumber": self.plate_number,
            "deviceId": self.device_id,
            "driverUserId": self.driver_user_id,
        }


@dataclass(frozen=True, slots=True)
class ShipmentTrackingRecord:
    shipment_id: str
    cargo_id: str
    task_id: str
    tenant_id: str
    owner_user_id: str
    driver_user_id: str
    warehouse_ids: tuple[str, ...]
    dispatch_region_ids: tuple[str, ...]
    transport_status: TransportTaskStatus
    vehicle: VehicleSummary

    @property
    def scope(self) -> ShipmentScope:
        return ShipmentScope(
            shipment_id=self.shipment_id,
            tenant_id=self.tenant_id,
            owner_user_id=self.owner_user_id,
            driver_user_id=self.driver_user_id,
            warehouse_ids=self.warehouse_ids,
            dispatch_region_ids=self.dispatch_region_ids,
        )


class ShipmentTrackingStore:
    """In-memory shipment index until CargoFlow's database layer is wired."""

    delay_threshold = timedelta(minutes=15)

    def __init__(
        self,
        records: tuple[ShipmentTrackingRecord, ...],
        aliases: dict[str, str] | None = None,
    ) -> None:
        self._records = {record.shipment_id: record for record in records}
        self._aliases = aliases or {}

    @classmethod
    def demo(cls) -> "ShipmentTrackingStore":
        record = ShipmentTrackingRecord(
            shipment_id="CGF-DEMO-001",
            cargo_id="cargo-demo-001",
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
        return cls((record,), aliases={"demo": record.shipment_id})

    def scope_for(self, shipment_id: str) -> ShipmentScope:
        return self._record_for(shipment_id).scope

    def latest_location_payload(
        self,
        shipment_id: str,
        principal: Principal,
        device_events: DeviceEventStore,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        record = self._record_for(shipment_id)
        require_shipment_access(principal, record.scope)

        latest = device_events.latest_location(record.task_id)
        checked_at = _as_utc(now or datetime.now(UTC))
        return {
            "shipmentId": record.shipment_id,
            "cargoId": record.cargo_id,
            "taskId": record.task_id,
            "tenantId": record.tenant_id,
            "transportStatus": record.transport_status.value,
            "vehicle": record.vehicle.to_wire(),
            "latestLocation": _latest_location_to_wire(latest),
            "delayHint": self._delay_hint(latest, checked_at),
            "access": {
                "role": principal.role.value,
                "principalId": principal.user_id,
            },
        }

    def _record_for(self, shipment_id: str) -> ShipmentTrackingRecord:
        normalized = self._aliases.get(shipment_id, shipment_id)
        try:
            return self._records[normalized]
        except KeyError as exc:
            raise ShipmentTrackingError(
                "shipment_not_found",
                f"No bound shipment found for {shipment_id}",
                HTTPStatus.NOT_FOUND,
            ) from exc

    def _delay_hint(
        self,
        latest: LatestLocationSnapshot | None,
        checked_at: datetime,
    ) -> dict[str, Any]:
        threshold_minutes = int(self.delay_threshold.total_seconds() // 60)
        if latest is None:
            return {
                "status": "missing",
                "isDelayed": True,
                "thresholdMinutes": threshold_minutes,
                "ageSeconds": None,
                "message": "No accepted GPS location is available for this bound cargo.",
            }

        age_seconds = max(0, int((checked_at - latest.reported_at).total_seconds()))
        is_delayed = age_seconds > int(self.delay_threshold.total_seconds())
        if is_delayed:
            message = "Latest location has not updated within the delay threshold."
            status = "delayed"
        else:
            message = "Latest location is current."
            status = "current"
        return {
            "status": status,
            "isDelayed": is_delayed,
            "thresholdMinutes": threshold_minutes,
            "ageSeconds": age_seconds,
            "lastUpdatedAt": latest.reported_at.isoformat(),
            "message": message,
        }


def _latest_location_to_wire(
    latest: LatestLocationSnapshot | None,
) -> dict[str, Any] | None:
    if latest is None:
        return None
    return {
        "longitude": latest.longitude,
        "latitude": latest.latitude,
        "capturedAt": latest.captured_at.isoformat(),
        "updatedAt": latest.reported_at.isoformat(),
        "eventId": latest.event_id,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)
