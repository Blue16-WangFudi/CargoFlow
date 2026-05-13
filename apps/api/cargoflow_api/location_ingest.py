"""Device telemetry ingestion rules for the current CargoFlow API skeleton."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4

from cargoflow_api.alert_rules import (
    AlertRuleEngine,
    AlertRuleStore,
    RoutePoint,
    TaskAlertContext,
    alert_to_wire,
)
from cargoflow_api.domain import (
    Alert,
    LocationPoint,
    TransportTaskStatus,
    VehicleOnlineStatus,
)


class DeviceEventError(Exception):
    """Raised when a device event cannot be accepted by the ingest API."""

    error_code = "invalid_device_event"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DeviceEventType(StrEnum):
    GPS = "gps"
    HEARTBEAT = "heartbeat"
    BOX_OPENED = "box_opened"
    BOX_CLOSED = "box_closed"

    @classmethod
    def from_wire(cls, value: object) -> "DeviceEventType":
        if not isinstance(value, str):
            raise DeviceEventError("eventType must be a string")
        normalized = value.strip().lower().replace("-", "_")
        aliases = {
            "gps": cls.GPS,
            "telemetry": cls.GPS,
            "location": cls.GPS,
            "heartbeat": cls.HEARTBEAT,
            "box_open": cls.BOX_OPENED,
            "box_opened": cls.BOX_OPENED,
            "open_box": cls.BOX_OPENED,
            "box_close": cls.BOX_CLOSED,
            "box_closed": cls.BOX_CLOSED,
            "close_box": cls.BOX_CLOSED,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise DeviceEventError(f"Unsupported eventType: {value}") from exc


@dataclass(frozen=True, slots=True)
class DeviceTaskBinding:
    device_id: str
    task_id: str
    vehicle_id: str


@dataclass(frozen=True, slots=True)
class LatestLocationSnapshot:
    task_id: str
    vehicle_id: str
    device_id: str
    longitude: float
    latitude: float
    captured_at: datetime
    reported_at: datetime
    event_id: str
    speed_kph: float | None = None

    @classmethod
    def from_point(cls, point: LocationPoint) -> "LatestLocationSnapshot":
        if point.event_id is None:
            raise ValueError("location point event_id is required for snapshots")
        return cls(
            task_id=point.task_id,
            vehicle_id=point.vehicle_id,
            device_id=point.device_id,
            longitude=point.longitude,
            latitude=point.latitude,
            captured_at=point.captured_at,
            reported_at=point.reported_at,
            event_id=point.event_id,
            speed_kph=point.speed_kph,
        )

    def to_wire(self) -> dict[str, Any]:
        payload = {
            "taskId": self.task_id,
            "vehicleId": self.vehicle_id,
            "deviceId": self.device_id,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "capturedAt": self.captured_at.isoformat(),
            "reportedAt": self.reported_at.isoformat(),
            "eventId": self.event_id,
        }
        if self.speed_kph is not None:
            payload["speedKph"] = self.speed_kph
        return payload


@dataclass(frozen=True, slots=True)
class SecurityEventSnapshot:
    task_id: str
    vehicle_id: str
    device_id: str
    event_type: DeviceEventType
    occurred_at: datetime
    reported_at: datetime
    event_id: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "vehicleId": self.vehicle_id,
            "deviceId": self.device_id,
            "eventType": self.event_type.value,
            "occurredAt": self.occurred_at.isoformat(),
            "reportedAt": self.reported_at.isoformat(),
            "eventId": self.event_id,
        }


@dataclass(frozen=True, slots=True)
class DeviceEventResult:
    event_id: str
    event_type: DeviceEventType
    task_id: str
    device_id: str
    received: bool
    latest_location_updated: bool
    latest_location: LatestLocationSnapshot | None = None
    ignored_reason: str | None = None
    generated_alerts: tuple[Alert, ...] = ()

    def to_wire(self) -> dict[str, Any]:
        payload = {
            "eventId": self.event_id,
            "eventType": self.event_type.value,
            "taskId": self.task_id,
            "deviceId": self.device_id,
            "received": self.received,
            "latestLocationUpdated": self.latest_location_updated,
        }
        if self.latest_location is not None:
            payload["latestLocation"] = self.latest_location.to_wire()
        if self.ignored_reason:
            payload["ignoredReason"] = self.ignored_reason
        if self.generated_alerts:
            payload["generatedAlerts"] = [
                alert_to_wire(alert) for alert in self.generated_alerts
            ]
        return payload


@dataclass(slots=True)
class DeviceState:
    binding: DeviceTaskBinding
    online_status: VehicleOnlineStatus = VehicleOnlineStatus.OFFLINE
    last_heartbeat_at: datetime | None = None
    security_events: list[SecurityEventSnapshot] = field(default_factory=list)


class DeviceEventStore:
    """In-memory ingest state until persistence and MQTT adapters are wired."""

    max_future_skew = timedelta(minutes=5)

    def __init__(
        self,
        bindings: tuple[DeviceTaskBinding, ...],
        *,
        alert_engine: AlertRuleEngine | None = None,
        task_alert_contexts: tuple[TaskAlertContext, ...] = (),
    ) -> None:
        self._states = {
            binding.device_id: DeviceState(binding=binding) for binding in bindings
        }
        self._alert_engine = alert_engine
        self._task_alert_contexts = {
            context.task_id: context for context in task_alert_contexts
        }
        self._latest_by_task: dict[str, LatestLocationSnapshot] = {}
        self._locations_by_task: dict[str, list[LatestLocationSnapshot]] = {}
        self._security_events_by_task: dict[str, list[SecurityEventSnapshot]] = {}
        self._seen_events: set[str] = set()
        self._lock = Lock()

    @classmethod
    def demo(cls) -> "DeviceEventStore":
        store = cls(
            (
                DeviceTaskBinding(
                    device_id="gps-demo-001",
                    task_id="task-demo-001",
                    vehicle_id="vehicle-demo-001",
                ),
            ),
            alert_engine=AlertRuleEngine(AlertRuleStore()),
            task_alert_contexts=(
                TaskAlertContext(
                    task_id="task-demo-001",
                    cargo_id="cargo-demo-001",
                    vehicle_id="vehicle-demo-001",
                    status=TransportTaskStatus.IN_TRANSIT,
                    route=(
                        RoutePoint(longitude=121.4737, latitude=31.2304),
                        RoutePoint(longitude=121.5956, latitude=31.3479),
                    ),
                ),
            ),
        )
        store.ingest(
            {
                "eventId": "evt-demo-seed-location",
                "eventType": "gps",
                "deviceId": "gps-demo-001",
                "taskId": "task-demo-001",
                "occurredAt": "2026-05-13T10:00:00+00:00",
                "reportedAt": "2026-05-13T10:00:03+00:00",
                "schemaVersion": 1,
                "longitude": 121.4737,
                "latitude": 31.2304,
                "speedKph": 48.5,
                "headingDegrees": 86,
            }
        )
        return store

    def ingest(self, payload: Mapping[str, Any]) -> DeviceEventResult:
        event = parse_device_event(payload)
        with self._lock:
            state = self._state_for(event.device_id)
            if event.task_id != state.binding.task_id:
                raise DeviceEventError("deviceId is not bound to the provided taskId")
            if event.event_id in self._seen_events:
                latest = self._latest_by_task.get(event.task_id)
                return DeviceEventResult(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    task_id=event.task_id,
                    device_id=event.device_id,
                    received=True,
                    latest_location_updated=False,
                    latest_location=latest,
                    ignored_reason="duplicate_event",
                )

            self._seen_events.add(event.event_id)
            if event.event_type is DeviceEventType.HEARTBEAT:
                state.online_status = VehicleOnlineStatus.ONLINE
                state.last_heartbeat_at = event.occurred_at
                return event.accepted_without_location()

            if event.event_type in {DeviceEventType.BOX_OPENED, DeviceEventType.BOX_CLOSED}:
                snapshot = SecurityEventSnapshot(
                    task_id=event.task_id,
                    vehicle_id=state.binding.vehicle_id,
                    device_id=event.device_id,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                    reported_at=event.reported_at,
                    event_id=event.event_id,
                )
                state.security_events.append(snapshot)
                self._security_events_by_task.setdefault(event.task_id, []).append(
                    snapshot
                )
                result = event.accepted_without_location()
                generated_alerts = self._evaluate_security_alerts(event)
                if not generated_alerts:
                    return result
                return DeviceEventResult(
                    event_id=result.event_id,
                    event_type=result.event_type,
                    task_id=result.task_id,
                    device_id=result.device_id,
                    received=result.received,
                    latest_location_updated=result.latest_location_updated,
                    generated_alerts=generated_alerts,
                )

            return self._ingest_gps(event, state.binding)

    def latest_location(self, task_id: str) -> LatestLocationSnapshot | None:
        with self._lock:
            return self._latest_by_task.get(task_id)

    def trajectory_points(self, task_id: str) -> tuple[LatestLocationSnapshot, ...]:
        with self._lock:
            points = self._locations_by_task.get(task_id, ())
            return tuple(sorted(points, key=lambda point: point.captured_at))

    def security_events(self, task_id: str) -> tuple[SecurityEventSnapshot, ...]:
        with self._lock:
            events = self._security_events_by_task.get(task_id, ())
            return tuple(sorted(events, key=lambda event: event.occurred_at))

    def _state_for(self, device_id: str) -> DeviceState:
        try:
            return self._states[device_id]
        except KeyError as exc:
            raise DeviceEventError("deviceId is not bound to an active task") from exc

    def _ingest_gps(
        self, event: "ParsedDeviceEvent", binding: DeviceTaskBinding
    ) -> DeviceEventResult:
        latest = self._latest_by_task.get(event.task_id)
        ignored_reason = event.location_ignore_reason(latest, self.max_future_skew)
        if ignored_reason is not None:
            return DeviceEventResult(
                event_id=event.event_id,
                event_type=event.event_type,
                task_id=event.task_id,
                device_id=event.device_id,
                received=True,
                latest_location_updated=False,
                latest_location=latest,
                ignored_reason=ignored_reason,
            )

        point = LocationPoint(
            id=f"loc-{uuid4().hex}",
            task_id=event.task_id,
            vehicle_id=binding.vehicle_id,
            device_id=event.device_id,
            longitude=event.longitude,
            latitude=event.latitude,
            captured_at=event.occurred_at,
            reported_at=event.reported_at,
            speed_kph=event.speed_kph,
            heading_degrees=event.heading_degrees,
            event_id=event.event_id,
            raw_payload=dict(event.raw_payload),
        )
        snapshot = LatestLocationSnapshot.from_point(point)
        self._latest_by_task[event.task_id] = snapshot
        self._locations_by_task.setdefault(event.task_id, []).append(snapshot)
        generated_alerts = self._evaluate_location_alerts(snapshot)
        return DeviceEventResult(
            event_id=event.event_id,
            event_type=event.event_type,
            task_id=event.task_id,
            device_id=event.device_id,
            received=True,
            latest_location_updated=True,
            latest_location=snapshot,
            generated_alerts=generated_alerts,
        )

    def _evaluate_location_alerts(
        self,
        snapshot: LatestLocationSnapshot,
    ) -> tuple[Alert, ...]:
        if self._alert_engine is None:
            return ()
        context = self._task_alert_contexts.get(snapshot.task_id)
        if context is None:
            return ()
        return self._alert_engine.evaluate_location(context, snapshot)

    def _evaluate_security_alerts(
        self,
        event: "ParsedDeviceEvent",
    ) -> tuple[Alert, ...]:
        if self._alert_engine is None:
            return ()
        context = self._task_alert_contexts.get(event.task_id)
        if context is None:
            return ()
        return self._alert_engine.evaluate_security_event(
            context,
            event_type=event.event_type,
            event_id=event.event_id,
            occurred_at=event.occurred_at,
        )


@dataclass(frozen=True, slots=True)
class ParsedDeviceEvent:
    event_id: str
    event_type: DeviceEventType
    device_id: str
    task_id: str
    occurred_at: datetime
    reported_at: datetime
    schema_version: int
    raw_payload: Mapping[str, Any]
    longitude: float = 0.0
    latitude: float = 0.0
    speed_kph: float | None = None
    heading_degrees: float | None = None
    has_location: bool = False

    def accepted_without_location(self) -> DeviceEventResult:
        return DeviceEventResult(
            event_id=self.event_id,
            event_type=self.event_type,
            task_id=self.task_id,
            device_id=self.device_id,
            received=True,
            latest_location_updated=False,
        )

    def location_ignore_reason(
        self,
        latest: LatestLocationSnapshot | None,
        max_future_skew: timedelta,
    ) -> str | None:
        if not self.has_location:
            return "missing_location"
        if not -180 <= self.longitude <= 180 or not -90 <= self.latitude <= 90:
            return "invalid_coordinates"
        if self.speed_kph is not None and self.speed_kph < 0:
            return "invalid_speed"
        if self.heading_degrees is not None and not 0 <= self.heading_degrees < 360:
            return "invalid_heading"
        if self.occurred_at > self.reported_at + max_future_skew:
            return "abnormal_capture_time"
        if latest is not None and self.occurred_at <= latest.captured_at:
            return "stale_capture_time"
        return None


def parse_device_event(payload: Mapping[str, Any]) -> ParsedDeviceEvent:
    event_type = DeviceEventType.from_wire(_required(payload, "eventType"))
    schema_version = _schema_version(_required(payload, "schemaVersion"))
    if schema_version != 1:
        raise DeviceEventError("Unsupported schemaVersion")

    occurred_at = _parse_datetime(_required(payload, "occurredAt"), "occurredAt")
    reported_at = _parse_datetime(_required(payload, "reportedAt"), "reportedAt")
    event_id = _required_text(payload, "eventId")
    device_id = _required_text(payload, "deviceId")
    task_id = _required_text(payload, "taskId")

    longitude = _optional_float(payload, "longitude")
    latitude = _optional_float(payload, "latitude")
    has_location = longitude is not None and latitude is not None
    if event_type is DeviceEventType.GPS and not has_location:
        return ParsedDeviceEvent(
            event_id=event_id,
            event_type=event_type,
            device_id=device_id,
            task_id=task_id,
            occurred_at=occurred_at,
            reported_at=reported_at,
            schema_version=schema_version,
            raw_payload=payload,
            has_location=False,
        )

    return ParsedDeviceEvent(
        event_id=event_id,
        event_type=event_type,
        device_id=device_id,
        task_id=task_id,
        occurred_at=occurred_at,
        reported_at=reported_at,
        schema_version=schema_version,
        raw_payload=payload,
        longitude=longitude if longitude is not None else 0.0,
        latitude=latitude if latitude is not None else 0.0,
        speed_kph=_optional_float(payload, "speedKph"),
        heading_degrees=_optional_float(payload, "headingDegrees"),
        has_location=has_location,
    )


def _required(payload: Mapping[str, Any], name: str) -> Any:
    if name in payload:
        return payload[name]
    snake_name = _camel_to_snake(name)
    if snake_name in payload:
        return payload[snake_name]
    raise DeviceEventError(f"Missing required field: {name}")


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = _required(payload, name)
    if not isinstance(value, str) or not value.strip():
        raise DeviceEventError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_float(payload: Mapping[str, Any], name: str) -> float | None:
    value = payload.get(name, payload.get(_camel_to_snake(name)))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeviceEventError(f"{name} must be a number")
    return float(value)


def _schema_version(value: object) -> int:
    if isinstance(value, bool):
        raise DeviceEventError("schemaVersion must be 1")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise DeviceEventError("schemaVersion must be 1")


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DeviceEventError(f"{field_name} must be an ISO-8601 datetime string")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise DeviceEventError(f"{field_name} must be an ISO-8601 datetime string") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


def _camel_to_snake(value: str) -> str:
    output: list[str] = []
    for character in value:
        if character.isupper():
            output.append("_")
            output.append(character.lower())
        else:
            output.append(character)
    return "".join(output).lstrip("_")
