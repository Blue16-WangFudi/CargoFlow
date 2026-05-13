"""Shipment latest-location query model for the current API skeleton."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from threading import RLock
from typing import Any
from uuid import uuid4

from cargoflow_api.access_control import Principal, ShipmentScope, require_shipment_access
from cargoflow_api.driver_status_reporting import (
    DriverStatusReportPayload,
    ensure_forward_transition,
    require_assigned_driver_report_access,
)
from cargoflow_api.domain import (
    AlertSeverity,
    AlertType,
    StatusReport,
    StatusReportState,
    TransportTaskStatus,
)
from cargoflow_api.eta import Destination, EtaService
from cargoflow_api.location_ingest import (
    DeviceEventStore,
    LatestLocationSnapshot,
    SecurityEventSnapshot,
)


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
    destination: Destination | None = None
    planned_start: Destination | None = None
    alert_points: tuple["TrajectoryAlertPoint", ...] = ()
    status_reports: tuple["TrajectoryStatusReportPoint", ...] = ()

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
        self._lock = RLock()

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
            destination=Destination(
                name="Shanghai Waigaoqiao Logistics Park",
                longitude=121.5956,
                latitude=31.3479,
            ),
            planned_start=Destination(
                name="Shanghai Pudong Warehouse",
                longitude=121.4737,
                latitude=31.2304,
            ),
            alert_points=(
                TrajectoryAlertPoint(
                    alert_id="alert-demo-box-001",
                    alert_type=AlertType.BOX_OPENED,
                    severity=AlertSeverity.HIGH,
                    status="pending",
                    longitude=121.52,
                    latitude=31.26,
                    triggered_at=datetime(2026, 5, 13, 10, 12, tzinfo=UTC),
                ),
            ),
            status_reports=(
                TrajectoryStatusReportPoint(
                    report_id="report-demo-loaded",
                    report_status=StatusReportState.LOADED,
                    reporter_user_id="driver-demo",
                    reported_at=datetime(2026, 5, 13, 9, 55, tzinfo=UTC),
                ),
            ),
        )
        return cls((record,), aliases={"demo": record.shipment_id})

    def scope_for(self, shipment_id: str) -> ShipmentScope:
        return self._record_for(shipment_id).scope

    def upsert_record(
        self,
        record: ShipmentTrackingRecord,
        *,
        aliases: tuple[str, ...] = (),
    ) -> None:
        with self._lock:
            self._records[record.shipment_id] = record
            for alias in aliases:
                if alias:
                    self._aliases[alias] = record.shipment_id

    def update_transport_status(
        self,
        shipment_id: str,
        status: TransportTaskStatus,
    ) -> ShipmentTrackingRecord:
        with self._lock:
            normalized = self._aliases.get(shipment_id, shipment_id)
            try:
                record = self._records[normalized]
            except KeyError as exc:
                raise ShipmentTrackingError(
                    "shipment_not_found",
                    f"No bound shipment found for {shipment_id}",
                    HTTPStatus.NOT_FOUND,
                ) from exc
            updated = replace(record, transport_status=status)
            self._records[normalized] = updated
            return updated

    def add_status_report(
        self,
        task_id: str,
        report_status: StatusReportState,
        reporter_user_id: str,
        reported_at: datetime,
        *,
        report_id: str | None = None,
        note: str | None = None,
    ) -> ShipmentTrackingRecord | None:
        with self._lock:
            for shipment_id, record in self._records.items():
                if record.task_id != task_id:
                    continue
                report = TrajectoryStatusReportPoint(
                    report_id=report_id or f"tracking-report-{uuid4().hex}",
                    report_status=report_status,
                    reporter_user_id=reporter_user_id,
                    reported_at=reported_at,
                    note=note,
                )
                updated = replace(
                    record,
                    transport_status=_transport_status_for_report(report_status),
                    status_reports=(*record.status_reports, report),
                )
                self._records[shipment_id] = updated
                return updated
        return None

    def sign_for_delivery(
        self,
        shipment_id: str,
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        record = self._record_for(shipment_id)
        require_shipment_access(principal, record.scope)
        if principal.user_id != record.owner_user_id:
            raise ShipmentTrackingError(
                "shipment_sign_access_denied",
                "Only the cargo owner can sign for this shipment.",
                HTTPStatus.FORBIDDEN,
            )
        if record.transport_status is TransportTaskStatus.SIGNED:
            signed_at = _as_utc(now or datetime.now(UTC))
            return _signed_payload(record, principal, signed_at)
        if record.transport_status is not TransportTaskStatus.DELIVERED:
            raise ShipmentTrackingError(
                "shipment_not_delivered",
                "Shipment can only be signed after the driver reports delivered.",
                HTTPStatus.CONFLICT,
            )

        signed_at = _as_utc(now or datetime.now(UTC))
        updated = self.update_transport_status(record.shipment_id, TransportTaskStatus.SIGNED)
        return _signed_payload(updated, principal, signed_at)

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

    def eta_payload(
        self,
        shipment_id: str,
        principal: Principal,
        device_events: DeviceEventStore,
        eta_service: EtaService,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        record = self._record_for(shipment_id)
        require_shipment_access(principal, record.scope)

        latest = device_events.latest_location(record.task_id)
        eta = eta_service.estimate(latest, record.destination, calculated_at=now)
        return {
            "shipmentId": record.shipment_id,
            "cargoId": record.cargo_id,
            "taskId": record.task_id,
            "tenantId": record.tenant_id,
            "transportStatus": record.transport_status.value,
            "eta": eta.to_wire(),
            "access": {
                "role": principal.role.value,
                "principalId": principal.user_id,
            },
        }

    def trajectory_payload(
        self,
        shipment_id: str,
        principal: Principal,
        device_events: DeviceEventStore,
    ) -> dict[str, Any]:
        record = self._record_for(shipment_id)
        require_shipment_access(principal, record.scope)

        location_points = device_events.trajectory_points(record.task_id)
        security_events = device_events.security_events(record.task_id)
        trajectory_points = _merge_trajectory_points(
            record,
            location_points,
            security_events,
        )
        return {
            "shipmentId": record.shipment_id,
            "cargoId": record.cargo_id,
            "taskId": record.task_id,
            "tenantId": record.tenant_id,
            "transportStatus": record.transport_status.value,
            "vehicle": record.vehicle.to_wire(),
            "trajectory": trajectory_points,
            "summary": {
                "pointCount": len(trajectory_points),
                "gpsPointCount": len(location_points),
                "alertPointCount": len(record.alert_points) + len(security_events),
                "statusReportCount": len(record.status_reports),
                "hasStartPoint": record.planned_start is not None,
                "hasEndPoint": record.destination is not None,
                "isSimplified": False,
            },
            "access": {
                "role": principal.role.value,
                "principalId": principal.user_id,
            },
        }

    def add_driver_status_report(
        self,
        shipment_id: str,
        principal: Principal,
        payload: DriverStatusReportPayload,
    ) -> tuple[StatusReport, ShipmentTrackingRecord]:
        with self._lock:
            record = self._record_for(shipment_id)
            require_assigned_driver_report_access(principal, record.scope)
            next_status = ensure_forward_transition(
                record.transport_status,
                payload.report_status,
            )
            report = StatusReport(
                id=f"report-{uuid4().hex}",
                task_id=record.task_id,
                report_status=payload.report_status,
                reporter_user_id=principal.user_id,
                reported_at=payload.reported_at,
                note=payload.note,
                attachment_urls=payload.attachment_urls,
                created_at=datetime.now(UTC).replace(microsecond=0),
            )
            updated = replace(
                record,
                transport_status=next_status,
                status_reports=(
                    *record.status_reports,
                    TrajectoryStatusReportPoint.from_status_report(report),
                ),
            )
            self._records[record.shipment_id] = updated
            return report, updated

    def _record_for(self, shipment_id: str) -> ShipmentTrackingRecord:
        with self._lock:
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


def _signed_payload(
    record: ShipmentTrackingRecord,
    principal: Principal,
    signed_at: datetime,
) -> dict[str, Any]:
    return {
        "shipment": {
            "shipmentId": record.shipment_id,
            "cargoId": record.cargo_id,
            "taskId": record.task_id,
            "transportStatus": record.transport_status.value,
            "signedAt": signed_at.isoformat(),
            "signedByUserId": principal.user_id,
        },
        "access": {
            "role": principal.role.value,
            "principalId": principal.user_id,
        },
    }


@dataclass(frozen=True, slots=True)
class TrajectoryAlertPoint:
    alert_id: str
    alert_type: AlertType
    severity: AlertSeverity
    status: str
    longitude: float
    latitude: float
    triggered_at: datetime

    def __post_init__(self) -> None:
        if not self.alert_id.strip():
            raise ValueError("alert_id must not be blank")
        if not -180 <= self.longitude <= 180:
            raise ValueError("alert longitude must be between -180 and 180")
        if not -90 <= self.latitude <= 90:
            raise ValueError("alert latitude must be between -90 and 90")

    def to_wire(self) -> dict[str, Any]:
        return {
            "kind": "alert",
            "alertId": self.alert_id,
            "alertType": self.alert_type.value,
            "severity": self.severity.value,
            "status": self.status,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "occurredAt": _as_utc(self.triggered_at).isoformat(),
            "isKeyNode": True,
        }


@dataclass(frozen=True, slots=True)
class TrajectoryStatusReportPoint:
    report_id: str
    report_status: StatusReportState
    reporter_user_id: str
    reported_at: datetime
    note: str | None = None
    attachment_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.report_id.strip():
            raise ValueError("report_id must not be blank")
        if not self.reporter_user_id.strip():
            raise ValueError("reporter_user_id must not be blank")

    @classmethod
    def from_status_report(cls, report: StatusReport) -> "TrajectoryStatusReportPoint":
        return cls(
            report_id=report.id,
            report_status=report.report_status,
            reporter_user_id=report.reporter_user_id,
            reported_at=report.reported_at,
            note=report.note,
            attachment_urls=report.attachment_urls,
        )

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": "status_report",
            "reportId": self.report_id,
            "reportStatus": self.report_status.value,
            "reporterUserId": self.reporter_user_id,
            "occurredAt": _as_utc(self.reported_at).isoformat(),
            "isKeyNode": True,
        }
        if self.note:
            payload["note"] = self.note
        if self.attachment_urls:
            payload["attachmentUrls"] = list(self.attachment_urls)
        return payload


def _merge_trajectory_points(
    record: ShipmentTrackingRecord,
    location_points: tuple[LatestLocationSnapshot, ...],
    security_events: tuple[SecurityEventSnapshot, ...],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    if record.planned_start is not None:
        points.append(_endpoint_to_wire("start", record.planned_start))

    for point in location_points:
        points.append(_trajectory_location_to_wire(point))
    for alert in record.alert_points:
        points.append(alert.to_wire())
    for event in security_events:
        points.append(_security_event_to_alert_wire(event))
    for report in record.status_reports:
        points.append(report.to_wire())

    if record.destination is not None:
        points.append(_endpoint_to_wire("end", record.destination))

    points.sort(key=_trajectory_sort_key)
    return points


def _trajectory_location_to_wire(point: LatestLocationSnapshot) -> dict[str, Any]:
    return {
        "kind": "gps",
        "eventId": point.event_id,
        "vehicleId": point.vehicle_id,
        "deviceId": point.device_id,
        "longitude": point.longitude,
        "latitude": point.latitude,
        "occurredAt": point.captured_at.isoformat(),
        "reportedAt": point.reported_at.isoformat(),
        "isKeyNode": False,
    }


def _security_event_to_alert_wire(event: SecurityEventSnapshot) -> dict[str, Any]:
    return {
        "kind": "alert",
        "alertId": event.event_id,
        "alertType": event.event_type.value,
        "severity": "high" if event.event_type.value == "box_opened" else "low",
        "status": "detected",
        "vehicleId": event.vehicle_id,
        "deviceId": event.device_id,
        "occurredAt": event.occurred_at.isoformat(),
        "reportedAt": event.reported_at.isoformat(),
        "isKeyNode": True,
    }


def _endpoint_to_wire(
    kind: str,
    endpoint: Destination,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": endpoint.name,
        "longitude": endpoint.longitude,
        "latitude": endpoint.latitude,
        "isKeyNode": True,
    }


def _trajectory_sort_key(point: dict[str, Any]) -> tuple[int, str, str]:
    if point["kind"] == "start":
        return (0, "", point["kind"])
    if point["kind"] == "end":
        return (2, "", point["kind"])
    return (1, str(point.get("occurredAt") or ""), point["kind"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def _transport_status_for_report(report_status: StatusReportState) -> TransportTaskStatus:
    return {
        StatusReportState.LOADED: TransportTaskStatus.LOADED,
        StatusReportState.IN_TRANSIT: TransportTaskStatus.IN_TRANSIT,
        StatusReportState.DELIVERED: TransportTaskStatus.DELIVERED,
    }[report_status]
