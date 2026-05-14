"""Driver task, command confirmation, and status reporting workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from http import HTTPStatus
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.domain import (
    DispatchCommand,
    DispatchCommandStatus,
    DispatchTargetType,
    StatusReport,
    StatusReportState,
    TransportTask,
    TransportTaskStatus,
)


class DriverWorkflowError(Exception):
    """Raised when a driver workflow request cannot be completed."""

    def __init__(self, error_code: str, message: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


class DriverWorkflowAuthorizationError(DriverWorkflowError):
    def __init__(self, message: str) -> None:
        super().__init__("driver_access_denied", message, HTTPStatus.FORBIDDEN)


class DriverWorkflowValidationError(DriverWorkflowError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_driver_action", message, HTTPStatus.BAD_REQUEST)


class DriverWorkflowConflictError(DriverWorkflowError):
    def __init__(self, message: str) -> None:
        super().__init__("driver_task_conflict", message, HTTPStatus.CONFLICT)


class DriverWorkflowNotFoundError(DriverWorkflowError):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(
            "driver_resource_not_found",
            f"No {resource} found for {resource_id}.",
            HTTPStatus.NOT_FOUND,
        )


@dataclass(frozen=True, slots=True)
class DriverTaskRecord:
    task: TransportTask
    shipment_id: str
    tenant_id: str
    cargo_number: str
    cargo_name: str
    vehicle_number: str
    plate_number: str
    origin: str
    destination: str


class DriverWorkflowStore:
    """In-memory driver workflow state until database persistence is wired."""

    def __init__(
        self,
        tasks: tuple[DriverTaskRecord, ...],
        *,
        commands: tuple[DispatchCommand, ...] = (),
        reports: tuple[StatusReport, ...] = (),
    ) -> None:
        self._tasks = {record.task.id: record for record in tasks}
        self._commands = {command.id: command for command in commands}
        self._reports_by_task: dict[str, list[StatusReport]] = {}
        for report in reports:
            self._reports_by_task.setdefault(report.task_id, []).append(report)
        self._lock = Lock()

    @classmethod
    def demo(cls) -> "DriverWorkflowStore":
        task = TransportTask(
            id="task-demo-001",
            task_number="TASK-DEMO-001",
            cargo_id="cargo-demo-001",
            vehicle_id="vehicle-demo-001",
            driver_user_id="driver-demo",
            origin="Shanghai Pudong Warehouse",
            destination="Shanghai Waigaoqiao Logistics Park",
            status=TransportTaskStatus.LOADED,
            planned_departure_at=_parse_datetime("2026-05-13T09:30:00+00:00"),
            planned_arrival_at=_parse_datetime("2026-05-13T13:30:00+00:00"),
            started_at=_parse_datetime("2026-05-13T10:00:00+00:00"),
        )
        command = DispatchCommand(
            id="cmd-demo-box-001",
            command_number="CMD-DEMO-BOX-001",
            task_id=task.id,
            alert_id="alert-demo-box-001",
            content="Inspect cargo seal and confirm box status.",
            created_by_user_id="dispatcher-demo",
            target_type=DispatchTargetType.DRIVER,
            target_id="driver-demo",
            status=DispatchCommandStatus.DELIVERED,
            issued_at=_parse_datetime("2026-05-13T10:13:00+00:00"),
            delivered_at=_parse_datetime("2026-05-13T10:13:10+00:00"),
        )
        report = StatusReport(
            id="report-demo-loaded",
            task_id=task.id,
            report_status=StatusReportState.LOADED,
            reporter_user_id="driver-demo",
            reported_at=_parse_datetime("2026-05-13T09:55:00+00:00"),
            note="Cargo loaded and temperature recorder checked.",
        )
        return cls(
            (
                DriverTaskRecord(
                    task=task,
                    shipment_id="CGF-DEMO-001",
                    tenant_id="cgf-demo",
                    cargo_number="CGO-DEMO-001",
                    cargo_name="Temperature controlled cargo",
                    vehicle_number="VH-DEMO-001",
                    plate_number="CF-2026",
                    origin=task.origin,
                    destination=task.destination,
                ),
            ),
            commands=(command,),
            reports=(report,),
        )

    def list_tasks(self, principal: Principal) -> dict[str, Any]:
        _require_driver(principal)
        with self._lock:
            records = [
                record
                for record in self._tasks.values()
                if record.tenant_id == principal.tenant_id
                and record.task.driver_user_id == principal.user_id
                and not record.task.is_terminal
            ]
            records.sort(key=lambda record: record.task.planned_arrival_at or _epoch())
            return {
                "tasks": [self._task_to_wire(record) for record in records],
                "count": len(records),
                "access": {
                    "role": principal.role.value,
                    "principalId": principal.user_id,
                },
            }

    def register_task(
        self,
        task: TransportTask,
        *,
        shipment_id: str,
        tenant_id: str,
        cargo_number: str,
        cargo_name: str,
        vehicle_number: str,
        plate_number: str,
        origin: str,
        destination: str,
    ) -> DriverTaskRecord:
        with self._lock:
            record = DriverTaskRecord(
                task=task,
                shipment_id=shipment_id,
                tenant_id=tenant_id,
                cargo_number=cargo_number,
                cargo_name=cargo_name,
                vehicle_number=vehicle_number,
                plate_number=plate_number,
                origin=origin,
                destination=destination,
            )
            self._tasks[task.id] = record
            return record

    def update_task_status(
        self,
        task_id: str,
        status: TransportTaskStatus,
        *,
        now: datetime | None = None,
    ) -> DriverTaskRecord:
        with self._lock:
            record = self._task_for(task_id)
            updated_at = _utc_now(now)
            updated_task = replace(
                record.task,
                status=status,
                updated_at=updated_at,
            )
            updated_record = replace(record, task=updated_task)
            self._tasks[task_id] = updated_record
            return updated_record

    def acknowledge_command(
        self,
        command_id: str,
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        _require_driver(principal)
        with self._lock:
            command = self._command_for(command_id)
            task = self._task_for(command.task_id)
            self._require_task_access(task, principal)
            if command.target_type is not DispatchTargetType.DRIVER:
                raise DriverWorkflowAuthorizationError(
                    "Only driver-targeted commands can be confirmed from the driver workspace."
                )
            if command.target_id != principal.user_id:
                raise DriverWorkflowAuthorizationError(
                    "Driver can only confirm commands targeted to them."
                )
            if command.status is DispatchCommandStatus.ACKNOWLEDGED:
                return {
                    "command": _command_to_wire(command),
                    "task": self._task_to_wire(task),
                }
            if command.status in {
                DispatchCommandStatus.FAILED,
                DispatchCommandStatus.REVOKED,
            }:
                raise DriverWorkflowConflictError(
                    "Terminal failed or revoked commands cannot be confirmed."
                )
            confirmed_at = _utc_now(now)
            updated = replace(
                command,
                status=DispatchCommandStatus.ACKNOWLEDGED,
                confirmed_at=confirmed_at,
                updated_at=confirmed_at,
            )
            self._commands[command.id] = updated
            return {
                "command": _command_to_wire(updated),
                "task": self._task_to_wire(task),
            }

    def create_status_report(
        self,
        task_id: str,
        payload: Mapping[str, Any],
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        _require_driver(principal)
        with self._lock:
            record = self._task_for(task_id)
            self._require_task_access(record, principal)
            report_status = _status_report_state(_required_text(payload, "reportStatus"))
            if not record.task.status.accepts_driver_report:
                raise DriverWorkflowConflictError(
                    "This transport task no longer accepts driver status reports."
                )

            current_rank = _task_status_rank(record.task.status)
            next_rank = _report_status_rank(report_status)
            if next_rank < current_rank:
                raise DriverWorkflowConflictError(
                    "Driver status reports cannot move a task backward."
                )

            reported_at = _utc_now(now)
            report = StatusReport(
                id=f"report-{uuid4().hex}",
                task_id=record.task.id,
                report_status=report_status,
                reporter_user_id=principal.user_id,
                reported_at=reported_at,
                note=_optional_text(_value(payload, "note", default=None)),
                attachment_urls=_attachment_urls(payload),
                created_at=reported_at,
            )
            self._reports_by_task.setdefault(record.task.id, []).append(report)
            updated_task = replace(
                record.task,
                status=_task_status_for_report(report_status),
                completed_at=reported_at
                if report_status is StatusReportState.DELIVERED
                else record.task.completed_at,
                updated_at=reported_at,
            )
            updated_record = replace(record, task=updated_task)
            self._tasks[updated_task.id] = updated_record
            return {
                "statusReport": _report_to_wire(report),
                "task": self._task_to_wire(updated_record),
            }

    def _task_to_wire(self, record: DriverTaskRecord) -> dict[str, Any]:
        reports = tuple(
            sorted(
                self._reports_by_task.get(record.task.id, ()),
                key=lambda report: report.reported_at,
            )
        )
        commands = tuple(
            sorted(
                (
                    command
                    for command in self._commands.values()
                    if command.task_id == record.task.id
                    and command.target_type is DispatchTargetType.DRIVER
                    and command.target_id == record.task.driver_user_id
                ),
                key=lambda command: command.issued_at,
            )
        )
        return {
            "taskId": record.task.id,
            "taskNumber": record.task.task_number,
            "shipmentId": record.shipment_id,
            "cargoId": record.task.cargo_id,
            "cargoNumber": record.cargo_number,
            "cargoName": record.cargo_name,
            "vehicleId": record.task.vehicle_id,
            "vehicleNumber": record.vehicle_number,
            "plateNumber": record.plate_number,
            "driverUserId": record.task.driver_user_id,
            "transportStatus": record.task.status.value,
            "origin": record.origin,
            "destination": record.destination,
            "plannedDepartureAt": _optional_datetime(record.task.planned_departure_at),
            "plannedArrivalAt": _optional_datetime(record.task.planned_arrival_at),
            "startedAt": _optional_datetime(record.task.started_at),
            "completedAt": _optional_datetime(record.task.completed_at),
            "commands": [_command_to_wire(command) for command in commands],
            "statusReports": [_report_to_wire(report) for report in reports],
            "summary": {
                "commandCount": len(commands),
                "unconfirmedCommandCount": sum(
                    1
                    for command in commands
                    if command.status
                    not in {
                        DispatchCommandStatus.ACKNOWLEDGED,
                        DispatchCommandStatus.FAILED,
                        DispatchCommandStatus.REVOKED,
                    }
                ),
                "reportCount": len(reports),
                "nextAllowedReports": _next_allowed_reports(record.task.status),
            },
        }

    def _task_for(self, task_id: str) -> DriverTaskRecord:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise DriverWorkflowNotFoundError("driver task", task_id) from exc

    def _command_for(self, command_id: str) -> DispatchCommand:
        try:
            return self._commands[command_id]
        except KeyError as exc:
            raise DriverWorkflowNotFoundError("dispatch command", command_id) from exc

    @staticmethod
    def _require_task_access(record: DriverTaskRecord, principal: Principal) -> None:
        if record.tenant_id != principal.tenant_id:
            raise DriverWorkflowAuthorizationError(
                "Driver is outside the transport task tenant scope."
            )
        if record.task.driver_user_id != principal.user_id:
            raise DriverWorkflowAuthorizationError(
                "Driver can only access their assigned transport tasks."
            )


def _command_to_wire(command: DispatchCommand) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "commandId": command.id,
        "commandNumber": command.command_number,
        "taskId": command.task_id,
        "alertId": command.alert_id,
        "content": command.content,
        "createdByUserId": command.created_by_user_id,
        "targetType": command.target_type.value,
        "targetId": command.target_id,
        "status": command.status.value,
        "issuedAt": command.issued_at.isoformat(),
    }
    if command.delivered_at is not None:
        payload["deliveredAt"] = command.delivered_at.isoformat()
    if command.confirmed_at is not None:
        payload["confirmedAt"] = command.confirmed_at.isoformat()
    if command.failed_at is not None:
        payload["failedAt"] = command.failed_at.isoformat()
    if command.revoked_at is not None:
        payload["revokedAt"] = command.revoked_at.isoformat()
    if command.failure_reason is not None:
        payload["failureReason"] = command.failure_reason
    return payload


def _report_to_wire(report: StatusReport) -> dict[str, Any]:
    return {
        "reportId": report.id,
        "taskId": report.task_id,
        "reportStatus": report.report_status.value,
        "reporterUserId": report.reporter_user_id,
        "reportedAt": report.reported_at.isoformat(),
        "note": report.note,
        "attachmentUrls": list(report.attachment_urls),
    }


def _require_driver(principal: Principal) -> None:
    if principal.role is not Role.DRIVER:
        raise DriverWorkflowAuthorizationError("Only drivers can use this workspace.")


def _task_status_for_report(report_status: StatusReportState) -> TransportTaskStatus:
    return {
        StatusReportState.LOADED: TransportTaskStatus.LOADED,
        StatusReportState.IN_TRANSIT: TransportTaskStatus.IN_TRANSIT,
        StatusReportState.DELIVERED: TransportTaskStatus.DELIVERED,
    }[report_status]


def _task_status_rank(status: TransportTaskStatus) -> int:
    if status in {TransportTaskStatus.PENDING_BINDING, TransportTaskStatus.BOUND}:
        return 0
    if status is TransportTaskStatus.LOADED:
        return 1
    if status is TransportTaskStatus.IN_TRANSIT:
        return 2
    if status is TransportTaskStatus.DELIVERED:
        return 3
    return 4


def _report_status_rank(status: StatusReportState) -> int:
    return {
        StatusReportState.LOADED: 1,
        StatusReportState.IN_TRANSIT: 2,
        StatusReportState.DELIVERED: 3,
    }[status]


def _next_allowed_reports(status: TransportTaskStatus) -> list[str]:
    current = _task_status_rank(status)
    return [
        report_status.value
        for report_status in StatusReportState
        if _report_status_rank(report_status) >= current
    ]


def _status_report_state(value: str) -> StatusReportState:
    normalized = value.strip().lower().replace("-", "_")
    try:
        return StatusReportState(normalized)
    except ValueError as exc:
        allowed = ", ".join(StatusReportState.ordered_values())
        raise DriverWorkflowValidationError(
            f"reportStatus must be one of: {allowed}."
        ) from exc


def _attachment_urls(payload: Mapping[str, Any]) -> tuple[str, ...]:
    value = _value(payload, "attachmentUrls", default=None)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise DriverWorkflowValidationError("attachmentUrls must be an array.")
    urls: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise DriverWorkflowValidationError(
                "attachmentUrls must contain non-empty strings."
            )
        urls.append(item.strip())
    return tuple(urls)


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = _value(payload, name, default=None)
    if not isinstance(value, str) or not value.strip():
        raise DriverWorkflowValidationError(f"{name} must be a non-empty string.")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DriverWorkflowValidationError("note must be a string.")
    return value.strip() or None


def _value(payload: Mapping[str, Any], name: str, default: Any = ...) -> Any:
    if name in payload:
        return payload[name]
    snake_name = _camel_to_snake(name)
    if snake_name in payload:
        return payload[snake_name]
    if default is ...:
        raise DriverWorkflowValidationError(f"Missing required field: {name}.")
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


def _optional_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _utc_now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def _epoch() -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC)


def _parse_datetime(value: str) -> datetime:
    return _utc_now(datetime.fromisoformat(value))
