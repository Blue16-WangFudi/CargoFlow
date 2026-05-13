"""Core domain records for CargoFlow.

The first implementation keeps these records dependency-free so the repository
still runs with the current standard-library skeleton. The migration in
``apps/api/migrations`` is the PostgreSQL persistence contract for the same
objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class TransportTaskStatus(StrEnum):
    PENDING_BINDING = "pending_binding"
    BOUND = "bound"
    LOADED = "loaded"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    SIGNED = "signed"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in {self.SIGNED, self.CANCELED}

    @property
    def accepts_driver_report(self) -> bool:
        return self in {self.BOUND, self.LOADED, self.IN_TRANSIT, self.DELIVERED}


class VehicleOnlineStatus(StrEnum):
    OFFLINE = "offline"
    ONLINE = "online"
    DELAYED = "delayed"


class VehicleBindingStatus(StrEnum):
    AVAILABLE = "available"
    BOUND = "bound"
    DISABLED = "disabled"


class AlertType(StrEnum):
    ROUTE_DEVIATION = "route_deviation"
    ABNORMAL_STOP = "abnormal_stop"
    BOX_OPENED = "box_opened"


class AlertSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AlertStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    CLOSED = "closed"
    FALSE_POSITIVE = "false_positive"

    @property
    def is_open(self) -> bool:
        return self in {self.PENDING, self.PROCESSING}


class DispatchCommandStatus(StrEnum):
    PENDING_DELIVERY = "pending_delivery"
    SENT = "sent"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    REVOKED = "revoked"

    @property
    def is_terminal(self) -> bool:
        return self in {self.ACKNOWLEDGED, self.FAILED, self.REVOKED}


class DispatchTargetType(StrEnum):
    DRIVER = "driver"
    VEHICLE = "vehicle"


class StatusReportState(StrEnum):
    LOADED = "loaded"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"

    @classmethod
    def ordered_values(cls) -> tuple[str, ...]:
        return tuple(state.value for state in cls)


class QaFeedback(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


def _require_text(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


@dataclass(frozen=True, slots=True)
class Cargo:
    id: str
    cargo_number: str
    owner_user_id: str
    name: str
    origin: str
    destination: str
    planned_departure_at: datetime | None = None
    planned_arrival_at: datetime | None = None
    current_status: TransportTaskStatus = TransportTaskStatus.PENDING_BINDING
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.cargo_number, "cargo_number")
        _require_text(self.owner_user_id, "owner_user_id")
        _require_text(self.name, "name")


@dataclass(frozen=True, slots=True)
class Vehicle:
    id: str
    warehouse_id: str
    vehicle_number: str
    plate_number: str
    device_id: str
    driver_user_id: str | None = None
    online_status: VehicleOnlineStatus = VehicleOnlineStatus.OFFLINE
    binding_status: VehicleBindingStatus = VehicleBindingStatus.AVAILABLE
    last_seen_at: datetime | None = None
    notes: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.warehouse_id, "warehouse_id")
        _require_text(self.vehicle_number, "vehicle_number")
        _require_text(self.plate_number, "plate_number")
        _require_text(self.device_id, "device_id")


@dataclass(frozen=True, slots=True)
class TransportTask:
    id: str
    task_number: str
    cargo_id: str
    vehicle_id: str
    driver_user_id: str
    origin: str
    destination: str
    planned_route: dict[str, Any] | None = None
    status: TransportTaskStatus = TransportTaskStatus.BOUND
    planned_departure_at: datetime | None = None
    planned_arrival_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.task_number, "task_number")
        _require_text(self.cargo_id, "cargo_id")
        _require_text(self.vehicle_id, "vehicle_id")
        _require_text(self.driver_user_id, "driver_user_id")

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @property
    def accepts_location_updates(self) -> bool:
        return not self.status.is_terminal


@dataclass(frozen=True, slots=True)
class LocationPoint:
    id: str
    task_id: str
    vehicle_id: str
    device_id: str
    longitude: float
    latitude: float
    captured_at: datetime
    reported_at: datetime
    speed_kph: float | None = None
    heading_degrees: float | None = None
    event_id: str | None = None
    raw_payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if self.speed_kph is not None and self.speed_kph < 0:
            raise ValueError("speed_kph must not be negative")
        if self.heading_degrees is not None and not 0 <= self.heading_degrees < 360:
            raise ValueError("heading_degrees must be in [0, 360)")
        _require_text(self.task_id, "task_id")
        _require_text(self.vehicle_id, "vehicle_id")
        _require_text(self.device_id, "device_id")


@dataclass(frozen=True, slots=True)
class Alert:
    id: str
    alert_number: str
    task_id: str
    cargo_id: str
    vehicle_id: str
    alert_type: AlertType
    severity: AlertSeverity
    longitude: float | None = None
    latitude: float | None = None
    status: AlertStatus = AlertStatus.PENDING
    triggered_at: datetime = field(default_factory=utc_now)
    handled_by_user_id: str | None = None
    handled_at: datetime | None = None
    closed_by_user_id: str | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    latest_evidence: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.alert_number, "alert_number")
        if self.longitude is not None and not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")
        if self.latitude is not None and not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")

    @property
    def is_open(self) -> bool:
        return self.status.is_open


@dataclass(frozen=True, slots=True)
class DispatchCommand:
    id: str
    command_number: str
    task_id: str
    content: str
    created_by_user_id: str
    target_type: DispatchTargetType
    target_id: str
    alert_id: str | None = None
    status: DispatchCommandStatus = DispatchCommandStatus.PENDING_DELIVERY
    issued_at: datetime = field(default_factory=utc_now)
    delivered_at: datetime | None = None
    confirmed_at: datetime | None = None
    failed_at: datetime | None = None
    revoked_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.command_number, "command_number")
        _require_text(self.task_id, "task_id")
        _require_text(self.content, "content")
        _require_text(self.created_by_user_id, "created_by_user_id")
        _require_text(self.target_id, "target_id")

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal


@dataclass(frozen=True, slots=True)
class StatusReport:
    id: str
    task_id: str
    report_status: StatusReportState
    reporter_user_id: str
    reported_at: datetime = field(default_factory=utc_now)
    note: str | None = None
    attachment_urls: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.reporter_user_id, "reporter_user_id")


@dataclass(frozen=True, slots=True)
class QaRecord:
    id: str
    user_id: str
    question: str
    answer: str | None = None
    citations: tuple[dict[str, str], ...] = ()
    session_id: str | None = None
    related_cargo_id: str | None = None
    related_task_id: str | None = None
    asked_at: datetime = field(default_factory=utc_now)
    answered_at: datetime | None = None
    feedback: QaFeedback | None = None
    failure_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.user_id, "user_id")
        _require_text(self.question, "question")
