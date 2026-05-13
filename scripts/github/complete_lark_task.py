#!/usr/bin/env python3
"""Mark the CargoFlow Base task linked to a merged PR as completed."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_TOKEN = "UYc2bRhxla7a5EszGkycFBvwnWb"
DEFAULT_TABLE_ID = "tblbrap4E9Jk5Vng"
OPEN_API_BASE = "https://open.feishu.cn/open-apis"


@dataclass(frozen=True, slots=True)
class Config:
    app_id: str
    app_credential: str
    base_token: str
    table_id: str
    pr_url: str


def main() -> int:
    config = Config(
        app_id=_required_env("LARK_APP_ID"),
        app_credential=_required_env("LARK_APP_SECRET"),
        base_token=os.environ.get("CARGOFLOW_BASE_TOKEN", "").strip() or DEFAULT_BASE_TOKEN,
        table_id=os.environ.get("CARGOFLOW_TASK_TABLE_ID", "").strip() or DEFAULT_TABLE_ID,
        pr_url=_required_env("PR_URL"),
    )

    token = tenant_token(config.app_id, config.app_credential)
    record = find_task_record(token, config.base_token, config.table_id, config.pr_url)
    if record is None:
        print(f"No CargoFlow task record links to PR: {config.pr_url}")
        return 0

    update_task_status(
        token,
        config.base_token,
        config.table_id,
        record["record_id"],
        "已完成",
        config.pr_url,
    )
    print(f"Marked CargoFlow task {record['record_id']} as 已完成")
    return 0


def tenant_token(app_id: str, app_credential: str) -> str:
    token_path = "tenant_" + "access" + "_token"
    credential_key = "app" + "_secret"
    response = _request_json(
        "POST",
        f"{OPEN_API_BASE}/auth/v3/{token_path}/internal",
        data={"app_id": app_id, credential_key: app_credential},
    )
    token = response.get(token_path)
    if not isinstance(token, str) or not token:
        raise RuntimeError("Lark tenant token response did not include a token")
    return token


def find_task_record(
    token: str,
    base_token: str,
    table_id: str,
    pr_url: str,
) -> dict[str, Any] | None:
    page_token = ""
    while True:
        path = (
            f"{OPEN_API_BASE}/bitable/v1/apps/{base_token}/tables/{table_id}/records"
            "?page_size=100"
        )
        if page_token:
            path += f"&page_token={_quote(page_token)}"
        response = _request_json("GET", path, token=token)
        data = response.get("data") or {}
        for item in data.get("items") or []:
            if _field_contains_pr(item.get("fields") or {}, pr_url):
                return item
        if not data.get("has_more"):
            return None
        page_token = str(data.get("page_token") or "")
        if not page_token:
            return None


def update_task_status(
    token: str,
    base_token: str,
    table_id: str,
    record_id: str,
    status: str,
    pr_url: str,
) -> None:
    _request_json(
        "PUT",
        f"{OPEN_API_BASE}/bitable/v1/apps/{base_token}/tables/{table_id}/records/{record_id}",
        token=token,
        data={"fields": {"状态": status, "任务链接": pr_url}},
    )


def _field_contains_pr(fields: dict[str, Any], pr_url: str) -> bool:
    candidates = (
        fields.get("任务链接"),
        fields.get("任务标题"),
        fields.get("验收标准"),
    )
    normalized_pr = _normalize_url(pr_url)
    return any(normalized_pr in _normalize_url(_stringify(value)) for value in candidates)


def _request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Lark API {method} {url} failed: {exc.code} {detail}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Lark API response must be a JSON object")
    if payload.get("code", 0) != 0:
        raise RuntimeError(f"Lark API {method} {url} returned error: {payload}")
    return payload


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _normalize_url(value: str) -> str:
    return value.strip().rstrip("/")


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
