"""Alert detection rules for CargoFlow's current in-memory API skeleton."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import atan2, cos, radians, sin, sqrt
from typing import Any
from uuid import uuid4

from cargoflow_api.domain import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AlertType,
    TransportTaskStatus,
)


@dataclass(frozen=True, slots=True)
class RoutePoint:
    longitude: float
    latitude: float


@dataclass(frozen=True, slots=True)
class TaskAlertContext:
    task_id: str
    cargo_id: str
    vehicle_id: str
    status: TransportTaskStatus
    route: tuple[RoutePoint, ...] = ()
    authorized_stops: tuple[RoutePoint, ...] = ()


@dataclass(frozen=True, slots=True)
class _TimedRuleState:
    started_at: datetime


class AlertRuleStore:
    """Keeps alerts and transient rule timers until persistence lands."""

    def __init__(self, alerts: tuple[Alert, ...] = ()) -> None:
        self._alerts: dict[str, Alert] = {}
        self._open_by_task_type: dict[tuple[str, AlertType], str] = {}
        for alert in alerts:
            self.save_alert(alert)

    def open_alerts(self, task_id: str) -> tuple[Alert, ...]:
        return tuple(alert for alert in self._alerts.values() if alert.task_id == task_id and alert.is_open)

    def alerts(self) -> tuple[Alert, ...]:
        return tuple(sorted(self._alerts.values(), key=lambda alert: alert.triggered_at))

    def get_alert(self, alert_id: str) -> Alert | None:
        return self._alerts.get(alert_id)

    def save_alert(self, alert: Alert) -> Alert:
        previous = self._alerts.get(alert.id)
        if previous is not None:
            previous_key = (previous.task_id, previous.alert_type)
            if self._open_by_task_type.get(previous_key) == previous.id:
                self._open_by_task_type.pop(previous_key, None)

        self._alerts[alert.id] = alert
        if alert.is_open:
            self._open_by_task_type[(alert.task_id, alert.alert_type)] = alert.id
        return alert

    def upsert_open_alert(
        self,
        context: TaskAlertContext,
        alert_type: AlertType,
        severity: AlertSeverity,
        *,
        triggered_at: datetime,
        longitude: float | None,
        latitude: float | None,
        evidence: dict[str, Any],
    ) -> Alert:
        key = (context.task_id, alert_type)
        existing_id = self._open_by_task_type.get(key)
        if existing_id is not None:
            existing = self._alerts.get(existing_id)
            if existing is not None and existing.is_open:
                updated = replace(
                    existing,
                    longitude=longitude,
                    latitude=latitude,
                    latest_evidence=evidence,
                    updated_at=triggered_at,
                )
                return self.save_alert(updated)
            self._open_by_task_type.pop(key, None)

        alert = Alert(
            id=f"alert-{uuid4().hex}",
            alert_number=f"ALR-{uuid4().hex[:12].upper()}",
            task_id=context.task_id,
            cargo_id=context.cargo_id,
            vehicle_id=context.vehicle_id,
            alert_type=alert_type,
            severity=severity,
            longitude=longitude,
            latitude=latitude,
            status=AlertStatus.PENDING,
            triggered_at=triggered_at,
            latest_evidence=evidence,
            created_at=triggered_at,
            updated_at=triggered_at,
        )
        return self.save_alert(alert)


class AlertRuleEngine:
    route_deviation_distance_m = 500.0
    route_deviation_duration = timedelta(minutes=3)
    abnormal_stop_speed_kph = 5.0
    abnormal_stop_duration = timedelta(minutes=30)
    authorized_stop_radius_m = 300.0

    def __init__(self, store: AlertRuleStore) -> None:
        self.store = store
        self._deviation_by_task: dict[str, _TimedRuleState] = {}
        self._stop_by_task: dict[str, _TimedRuleState] = {}

    def evaluate_location(
        self,
        context: TaskAlertContext,
        point: Any,
    ) -> tuple[Alert, ...]:
        if context.status.is_terminal:
            return ()

        alerts: list[Alert] = []
        deviation = self._evaluate_route_deviation(context, point)
        if deviation is not None:
            alerts.append(deviation)
        stop = self._evaluate_abnormal_stop(context, point)
        if stop is not None:
            alerts.append(stop)
        return tuple(alerts)

    def evaluate_security_event(
        self,
        context: TaskAlertContext,
        *,
        event_type: Any,
        event_id: str,
        occurred_at: datetime,
    ) -> tuple[Alert, ...]:
        if context.status.is_terminal:
            return ()
        if str(getattr(event_type, "value", event_type)) != "box_opened":
            return ()

        alert = self.store.upsert_open_alert(
            context,
            AlertType.BOX_OPENED,
            AlertSeverity.HIGH,
            triggered_at=occurred_at,
            longitude=None,
            latitude=None,
            evidence={
                "eventId": event_id,
                "eventType": "box_opened",
                "reason": "Unauthorized box opening during active transport.",
            },
        )
        return (alert,)

    def _evaluate_route_deviation(
        self,
        context: TaskAlertContext,
        point: Any,
    ) -> Alert | None:
        if len(context.route) < 2:
            self._deviation_by_task.pop(context.task_id, None)
            return None

        distance_m = _distance_to_route_m(point.longitude, point.latitude, context.route)
        if distance_m <= self.route_deviation_distance_m:
            self._deviation_by_task.pop(context.task_id, None)
            return None

        state = self._deviation_by_task.setdefault(
            context.task_id,
            _TimedRuleState(started_at=point.captured_at),
        )
        duration = point.captured_at - state.started_at
        if duration < self.route_deviation_duration:
            return None

        return self.store.upsert_open_alert(
            context,
            AlertType.ROUTE_DEVIATION,
            AlertSeverity.MEDIUM,
            triggered_at=state.started_at,
            longitude=point.longitude,
            latitude=point.latitude,
            evidence={
                "eventId": point.event_id,
                "distanceMeters": round(distance_m, 1),
                "durationSeconds": int(duration.total_seconds()),
                "thresholdMeters": int(self.route_deviation_distance_m),
                "thresholdSeconds": int(self.route_deviation_duration.total_seconds()),
            },
        )

    def _evaluate_abnormal_stop(
        self,
        context: TaskAlertContext,
        point: Any,
    ) -> Alert | None:
        speed = point.speed_kph
        if speed is None or speed > self.abnormal_stop_speed_kph:
            self._stop_by_task.pop(context.task_id, None)
            return None
        if _is_authorized_stop(
            point.longitude,
            point.latitude,
            context.route,
            context.authorized_stops,
            self.authorized_stop_radius_m,
        ):
            self._stop_by_task.pop(context.task_id, None)
            return None

        state = self._stop_by_task.setdefault(
            context.task_id,
            _TimedRuleState(started_at=point.captured_at),
        )
        duration = point.captured_at - state.started_at
        if duration < self.abnormal_stop_duration:
            return None

        return self.store.upsert_open_alert(
            context,
            AlertType.ABNORMAL_STOP,
            AlertSeverity.MEDIUM,
            triggered_at=state.started_at,
            longitude=point.longitude,
            latitude=point.latitude,
            evidence={
                "eventId": point.event_id,
                "speedKph": speed,
                "durationSeconds": int(duration.total_seconds()),
                "thresholdSpeedKph": self.abnormal_stop_speed_kph,
                "thresholdSeconds": int(self.abnormal_stop_duration.total_seconds()),
            },
        )


def alert_to_wire(alert: Alert) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "alertId": alert.id,
        "alertNumber": alert.alert_number,
        "taskId": alert.task_id,
        "cargoId": alert.cargo_id,
        "vehicleId": alert.vehicle_id,
        "alertType": alert.alert_type.value,
        "severity": alert.severity.value,
        "status": alert.status.value,
        "triggeredAt": alert.triggered_at.isoformat(),
    }
    if alert.longitude is not None:
        payload["longitude"] = alert.longitude
    if alert.latitude is not None:
        payload["latitude"] = alert.latitude
    if alert.latest_evidence is not None:
        payload["latestEvidence"] = alert.latest_evidence
    if alert.handled_by_user_id is not None:
        payload["handledByUserId"] = alert.handled_by_user_id
    if alert.handled_at is not None:
        payload["handledAt"] = alert.handled_at.isoformat()
    if alert.closed_by_user_id is not None:
        payload["closedByUserId"] = alert.closed_by_user_id
    if alert.closed_at is not None:
        payload["closedAt"] = alert.closed_at.isoformat()
    if alert.close_reason is not None:
        payload["closeReason"] = alert.close_reason
    return payload


def _distance_to_route_m(
    longitude: float,
    latitude: float,
    route: tuple[RoutePoint, ...],
) -> float:
    return min(
        _distance_to_segment_m(longitude, latitude, start, end)
        for start, end in zip(route, route[1:])
    )


def _distance_to_segment_m(
    longitude: float,
    latitude: float,
    start: RoutePoint,
    end: RoutePoint,
) -> float:
    reference_lat = radians((latitude + start.latitude + end.latitude) / 3)
    px, py = _to_local_m(longitude, latitude, reference_lat)
    ax, ay = _to_local_m(start.longitude, start.latitude, reference_lat)
    bx, by = _to_local_m(end.longitude, end.latitude, reference_lat)
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    nearest_x = ax + t * dx
    nearest_y = ay + t * dy
    return sqrt((px - nearest_x) ** 2 + (py - nearest_y) ** 2)


def _to_local_m(longitude: float, latitude: float, reference_lat: float) -> tuple[float, float]:
    earth_radius_m = 6_371_000
    return (
        radians(longitude) * earth_radius_m * cos(reference_lat),
        radians(latitude) * earth_radius_m,
    )


def _is_authorized_stop(
    longitude: float,
    latitude: float,
    route: tuple[RoutePoint, ...],
    authorized_stops: tuple[RoutePoint, ...],
    radius_m: float,
) -> bool:
    stop_points = tuple(route[:1] + route[-1:]) + authorized_stops
    return any(_haversine_m(longitude, latitude, point.longitude, point.latitude) <= radius_m for point in stop_points)


def _haversine_m(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    earth_radius_m = 6_371_000
    phi_a = radians(lat_a)
    phi_b = radians(lat_b)
    delta_phi = radians(lat_b - lat_a)
    delta_lambda = radians(lon_b - lon_a)
    a = sin(delta_phi / 2) ** 2 + cos(phi_a) * cos(phi_b) * sin(delta_lambda / 2) ** 2
    return 2 * earth_radius_m * atan2(sqrt(a), sqrt(1 - a))
