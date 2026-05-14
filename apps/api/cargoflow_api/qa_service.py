"""Deterministic Q&A interface and record store for CargoFlow."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from http import HTTPStatus
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4

from cargoflow_api.access_control import Principal
from cargoflow_api.domain import QaFeedback, QaRecord
from cargoflow_api.qa_context import BusinessContextFilter, BusinessContextKind


class QaServiceError(Exception):
    """Raised when a Q&A request cannot be completed."""

    def __init__(self, error_code: str, message: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


class QaValidationError(QaServiceError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_qa_request", message, HTTPStatus.BAD_REQUEST)


class QaAuthorizationError(QaServiceError):
    def __init__(self, message: str) -> None:
        super().__init__("qa_access_denied", message, HTTPStatus.FORBIDDEN)


class QaNotFoundError(QaServiceError):
    def __init__(self, record_id: str) -> None:
        super().__init__(
            "qa_record_not_found",
            f"No Q&A record found for {record_id}.",
            HTTPStatus.NOT_FOUND,
        )


class QaRecordStore:
    """In-memory Q&A record store until PostgreSQL persistence is wired."""

    def __init__(self, records: tuple[QaRecord, ...] = ()) -> None:
        self._records = {record.id: record for record in records}
        self._lock = Lock()

    def create_pending(
        self,
        *,
        principal: Principal,
        question: str,
        session_id: str | None,
        asked_at: datetime,
    ) -> QaRecord:
        record = QaRecord(
            id=f"qa-{uuid4().hex}",
            user_id=principal.user_id,
            question=question,
            session_id=session_id,
            asked_at=asked_at,
            created_at=asked_at,
        )
        with self._lock:
            self._records[record.id] = record
        return record

    def save(self, record: QaRecord) -> QaRecord:
        with self._lock:
            self._records[record.id] = record
        return record

    def get_for_principal(self, record_id: str, principal: Principal) -> QaRecord:
        with self._lock:
            record = self._records.get(record_id)
        if record is None:
            raise QaNotFoundError(record_id)
        if record.user_id != principal.user_id:
            raise QaAuthorizationError("Users can only read their own Q&A records.")
        return record

    def list_for_principal(
        self,
        principal: Principal,
        *,
        session_id: str | None = None,
        limit: int = 20,
    ) -> tuple[QaRecord, ...]:
        with self._lock:
            records = [
                record
                for record in self._records.values()
                if record.user_id == principal.user_id
                and (session_id is None or record.session_id == session_id)
            ]
        records.sort(key=lambda record: record.asked_at, reverse=True)
        return tuple(records[:limit])

    def apply_feedback(
        self,
        record_id: str,
        payload: Mapping[str, Any],
        principal: Principal,
    ) -> QaRecord:
        record = self.get_for_principal(record_id, principal)
        feedback = _feedback_from_wire(_required_text(payload, "feedback"))
        updated = replace(record, feedback=feedback)
        return self.save(updated)


class QaService:
    """Answers phase-one CargoFlow questions with auditable records."""

    def __init__(
        self,
        *,
        context_filter: BusinessContextFilter,
        record_store: QaRecordStore | None = None,
    ) -> None:
        self.context_filter = context_filter
        self.records = record_store or QaRecordStore()

    def ask(
        self,
        payload: Mapping[str, Any],
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        question = _required_text(payload, "question")
        session_id = _optional_text(_value(payload, "sessionId", default=None))
        requested_ids = _string_tuple(_value(payload, "requestedIds", default=()))
        requested_types = _requested_types(_value(payload, "requestedTypes", default=()))
        asked_at = _utc_now(now)
        record = self.records.create_pending(
            principal=principal,
            question=question,
            session_id=session_id,
            asked_at=asked_at,
        )

        refs = self.context_filter.authorized_refs(
            principal,
            requested_ids=requested_ids,
            requested_types=requested_types,
        )
        authorization_summary = self.context_filter.authorization_summary(
            principal,
            requested_ids=requested_ids,
            requested_types=requested_types,
        )
        answer = _answer_question(question, refs, bool(requested_ids or requested_types))
        answered_at = _utc_now(now)
        updated = replace(
            record,
            answer=answer["answer"],
            citations=tuple(answer["sources"]),
            related_cargo_id=_first_ref_id(refs, "cargo"),
            related_task_id=_first_ref_id(refs, "transport_task"),
            answered_at=answered_at,
            failure_reason=answer["failureReason"],
        )
        self.records.save(updated)
        return {
            **_record_to_wire(updated),
            "sources": answer["sources"],
            "businessRefs": [ref.to_wire() for ref in refs],
            "authorization": authorization_summary,
            "confidence": answer["confidence"],
        }

    def list_records(self, principal: Principal, params: Mapping[str, str]) -> dict[str, Any]:
        session_id = _optional_text(params.get("sessionId") or params.get("session_id"))
        limit = _limit_from_query(params.get("limit"))
        records = self.records.list_for_principal(
            principal,
            session_id=session_id,
            limit=limit,
        )
        return {
            "records": [_record_to_wire(record) for record in records],
            "count": len(records),
            "filters": {
                **({"sessionId": session_id} if session_id else {}),
                "limit": limit,
            },
        }

    def get_record(self, record_id: str, principal: Principal) -> dict[str, Any]:
        return {"record": _record_to_wire(self.records.get_for_principal(record_id, principal))}

    def apply_feedback(
        self,
        record_id: str,
        payload: Mapping[str, Any],
        principal: Principal,
    ) -> dict[str, Any]:
        return {"record": _record_to_wire(self.records.apply_feedback(record_id, payload, principal))}


def _answer_question(
    question: str,
    refs: tuple[Any, ...],
    requested_business_context: bool,
) -> dict[str, Any]:
    normalized = question.lower()
    if _contains_any(normalized, ("ignore", "bypass", "越权", "未授权", "密钥", "token")):
        return _refusal(
            "该信息不在可回答范围内，无法处理绕过权限或获取敏感配置的请求。",
            failure_reason="disallowed_request",
        )
    if requested_business_context and not refs:
        return _refusal(
            "我不能查看你未授权的货物或运输任务。",
            failure_reason="unauthorized_business_context",
        )
    if _contains_any(normalized, ("偏航", "route deviation", "deviation")):
        return {
            "answer": "偏航告警按计划路线与实时位置偏差触发，当前一期规则要求持续偏离路线后生成告警，具体阈值以告警规则配置为准。",
            "sources": (_knowledge_source("FR-04 异常报警"),),
            "confidence": "high",
            "failureReason": None,
        }
    if _contains_any(normalized, ("司机", "指令", "确认", "上报", "status report", "command")):
        return {
            "answer": "司机工作台支持查看本人任务、确认调度指令，并按已装货、运输中、已送达顺序提交状态上报；状态上报会记录人员、时间和备注。",
            "sources": (_knowledge_source("FR-08/09 司机指令与状态上报"),),
            "confidence": "high",
            "failureReason": None,
        }
    if refs:
        return _business_answer(refs)
    return _refusal(
        "当前知识库无法确认该问题，请联系运营或调度确认。",
        failure_reason="insufficient_sources",
    )


def _business_answer(refs: tuple[Any, ...]) -> dict[str, Any]:
    displays = ", ".join(ref.display for ref in refs[:4])
    status_bits = []
    for ref in refs:
        status = ref.data.get("status")
        if status:
            status_bits.append(f"{ref.display} 当前状态为 {status}")
    status_sentence = "；".join(status_bits[:3])
    answer = f"已按你的权限找到 {len(refs)} 条相关业务记录：{displays}。"
    if status_sentence:
        answer = f"{answer}{status_sentence}。"
    return {
        "answer": answer,
        "sources": (_business_source(),),
        "confidence": "medium",
        "failureReason": None,
    }


def _refusal(answer: str, *, failure_reason: str) -> dict[str, Any]:
    return {
        "answer": answer,
        "sources": (),
        "confidence": "low",
        "failureReason": failure_reason,
    }


def _knowledge_source(section: str) -> dict[str, str]:
    return {
        "type": "knowledge_doc",
        "title": "智慧物流 PRD 需求文档",
        "path": "docs/智慧物流_PRD需求文档.md",
        "section": section,
        "version": "v1.0",
    }


def _business_source() -> dict[str, str]:
    return {
        "type": "business_context",
        "title": "授权业务上下文",
        "path": "api://business-context",
        "section": "request_scope",
        "version": "runtime",
    }


def _record_to_wire(record: QaRecord) -> dict[str, Any]:
    return {
        "recordId": record.id,
        "userId": record.user_id,
        "question": record.question,
        "answer": record.answer,
        "sources": list(record.citations),
        "sessionId": record.session_id,
        "relatedCargoId": record.related_cargo_id,
        "relatedTaskId": record.related_task_id,
        "askedAt": record.asked_at.isoformat(),
        "answeredAt": record.answered_at.isoformat() if record.answered_at else None,
        "feedback": record.feedback.value if record.feedback else None,
        "failureReason": record.failure_reason,
        "createdAt": record.created_at.isoformat(),
    }


def _first_ref_id(refs: tuple[Any, ...], ref_type: str) -> str | None:
    for ref in refs:
        if ref.type == ref_type:
            return ref.id
    return None


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = _value(payload, name, default=None)
    if not isinstance(value, str) or not value.strip():
        raise QaValidationError(f"{name} must be a non-empty string.")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise QaValidationError("sessionId must be a string.")
    return value.strip() or None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)):
        raise QaValidationError("requestedIds must be an array of strings.")
    output = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise QaValidationError("requestedIds must contain non-empty strings.")
        output.append(item.strip())
    return tuple(output)


def _requested_types(value: Any) -> tuple[BusinessContextKind, ...]:
    raw_types = _string_tuple(value)
    allowed = {"cargo", "transport_task", "vehicle", "alert"}
    invalid = [item for item in raw_types if item not in allowed]
    if invalid:
        raise QaValidationError(
            f"requestedTypes must contain only: {', '.join(sorted(allowed))}."
        )
    return raw_types  # type: ignore[return-value]


def _feedback_from_wire(value: str) -> QaFeedback:
    normalized = value.strip().lower().replace("-", "_")
    try:
        return QaFeedback(normalized)
    except ValueError as exc:
        raise QaValidationError("feedback must be helpful or not_helpful.") from exc


def _limit_from_query(value: str | None) -> int:
    if value is None or not value.strip():
        return 20
    try:
        limit = int(value)
    except ValueError as exc:
        raise QaValidationError("limit must be an integer.") from exc
    if not 1 <= limit <= 100:
        raise QaValidationError("limit must be between 1 and 100.")
    return limit


def _value(payload: Mapping[str, Any], name: str, default: Any = ...) -> Any:
    if name in payload:
        return payload[name]
    snake_name = _camel_to_snake(name)
    if snake_name in payload:
        return payload[snake_name]
    if default is ...:
        raise QaValidationError(f"Missing required field: {name}.")
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


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _utc_now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)
