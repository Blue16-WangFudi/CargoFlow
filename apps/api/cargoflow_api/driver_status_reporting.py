"""Driver status reporting rules for the current CargoFlow API skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, Mapping
from urllib.parse import urlparse

from cargoflow_api.access_control import Principal, Role, ShipmentScope
from cargoflow_api.domain import StatusReport, StatusReportState, TransportTaskStatus


class DriverStatusReportError(Exception):
    """Raised when a driver status report cannot be accepted."""

    def __init__(self, error_code: str, message: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


class DriverStatusReportValidationError(DriverStatusReportError):
    def __init__(self, message: str) -> None:
        super().__init__(
            "invalid_driver_status_report",
            message,
            HTTPStatus.BAD_REQUEST,
        )


class DriverStatusReportAuthorizationError(DriverStatusReportError):
    def __init__(self) -> None:
        super().__init__(
            "driver_report_access_denied",
            "Only the assigned driver can report status for this transport task.",
            HTTPStatus.FORBIDDEN,
        )


class DriverStatusReportConflictError(DriverStatusReportError):
    def __init__(self, message: str) -> None:
        super().__init__("driver_status_conflict", message, HTTPStatus.CONFLICT)


@dataclass(frozen=True, slots=True)
class DriverStatusReportPayload:
    report_status: StatusReportState
    reported_at: datetime
    note: str | None
    attachment_urls: tuple[str, ...]


REPORT_STATUS_TO_TASK_STATUS = {
    StatusReportState.LOADED: TransportTaskStatus.LOADED,
    StatusReportState.IN_TRANSIT: TransportTaskStatus.IN_TRANSIT,
    StatusReportState.DELIVERED: TransportTaskStatus.DELIVERED,
}
TASK_STATUS_ORDER = {
    TransportTaskStatus.BOUND: -1,
    TransportTaskStatus.LOADED: 0,
    TransportTaskStatus.IN_TRANSIT: 1,
    TransportTaskStatus.DELIVERED: 2,
}
REPORT_STATUS_ORDER = {
    StatusReportState.LOADED: 0,
    StatusReportState.IN_TRANSIT: 1,
    StatusReportState.DELIVERED: 2,
}


def require_assigned_driver_report_access(
    principal: Principal,
    shipment: ShipmentScope,
) -> None:
    if (
        principal.tenant_id != shipment.tenant_id
        or principal.role is not Role.DRIVER
        or principal.user_id != shipment.driver_user_id
    ):
        raise DriverStatusReportAuthorizationError()


def parse_driver_status_report_payload(
    payload: Mapping[str, Any],
    *,
    now: datetime,
) -> DriverStatusReportPayload:
    return DriverStatusReportPayload(
        report_status=_status_from_wire(_required(payload, "reportStatus")),
        reported_at=_optional_datetime(payload, "reportedAt") or _as_utc(now),
        note=_optional_note(payload),
        attachment_urls=_attachment_urls(payload),
    )


def ensure_forward_transition(
    current_status: TransportTaskStatus,
    requested_status: StatusReportState,
) -> TransportTaskStatus:
    if current_status.is_terminal or current_status is TransportTaskStatus.PENDING_BINDING:
        raise DriverStatusReportConflictError(
            f"Task status {current_status.value} cannot accept driver reports."
        )
    current_order = TASK_STATUS_ORDER.get(current_status)
    if current_order is None:
        raise DriverStatusReportConflictError(
            f"Task status {current_status.value} cannot accept driver reports."
        )
    requested_order = REPORT_STATUS_ORDER[requested_status]
    if requested_order != current_order + 1:
        raise DriverStatusReportConflictError(
            "Reported status must be the next forward task status."
        )
    return REPORT_STATUS_TO_TASK_STATUS[requested_status]


def status_report_to_wire(report: StatusReport) -> dict[str, Any]:
    return {
        "reportId": report.id,
        "taskId": report.task_id,
        "reportStatus": report.report_status.value,
        "reporterUserId": report.reporter_user_id,
        "reportedAt": _as_utc(report.reported_at).isoformat(),
        "note": report.note,
        "attachmentUrls": list(report.attachment_urls),
        "createdAt": _as_utc(report.created_at).isoformat(),
    }


def _status_from_wire(value: Any) -> StatusReportState:
    if not isinstance(value, str):
        raise DriverStatusReportValidationError("reportStatus must be a string.")
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "loaded": StatusReportState.LOADED,
        "已装货": StatusReportState.LOADED,
        "in_transit": StatusReportState.IN_TRANSIT,
        "transporting": StatusReportState.IN_TRANSIT,
        "运输中": StatusReportState.IN_TRANSIT,
        "delivered": StatusReportState.DELIVERED,
        "arrived": StatusReportState.DELIVERED,
        "已送达": StatusReportState.DELIVERED,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        allowed = ", ".join(StatusReportState.ordered_values())
        raise DriverStatusReportValidationError(
            f"reportStatus must be one of: {allowed}."
        ) from exc


def _required(payload: Mapping[str, Any], name: str) -> Any:
    if name in payload:
        return payload[name]
    snake_name = _camel_to_snake(name)
    if snake_name in payload:
        return payload[snake_name]
    if name == "reportStatus" and "status" in payload:
        return payload["status"]
    raise DriverStatusReportValidationError(f"Missing required field: {name}.")


def _optional_note(payload: Mapping[str, Any]) -> str | None:
    value = _optional(payload, "note")
    if value is None:
        value = _optional(payload, "remark")
    if value is None:
        return None
    if not isinstance(value, str):
        raise DriverStatusReportValidationError("note must be a string or null.")
    stripped = value.strip()
    return stripped or None


def _attachment_urls(payload: Mapping[str, Any]) -> tuple[str, ...]:
    value = _optional(payload, "attachmentUrls")
    if value is None:
        value = _optional(payload, "attachments")
    if value is None:
        return ()
    if not isinstance(value, list):
        raise DriverStatusReportValidationError(
            "attachmentUrls must be an array of URLs."
        )
    urls: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise DriverStatusReportValidationError(
                "attachmentUrls must contain non-empty URL strings."
            )
        clean = item.strip()
        parsed = urlparse(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DriverStatusReportValidationError(
                "attachmentUrls must contain absolute http or https URLs."
            )
        urls.append(clean)
    return tuple(urls)


def _optional_datetime(payload: Mapping[str, Any], name: str) -> datetime | None:
    value = _optional(payload, name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DriverStatusReportValidationError(
            f"{name} must be an ISO-8601 datetime string."
        )
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise DriverStatusReportValidationError(
            f"{name} must be an ISO-8601 datetime string."
        ) from exc
    return _as_utc(parsed)


def _optional(payload: Mapping[str, Any], name: str) -> Any:
    if name in payload:
        return payload[name]
    snake_name = _camel_to_snake(name)
    if snake_name in payload:
        return payload[snake_name]
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def _camel_to_snake(value: str) -> str:
    output: list[str] = []
    for character in value:
        if character.isupper():
            output.append("_")
            output.append(character.lower())
        else:
            output.append(character)
    return "".join(output).lstrip("_")
