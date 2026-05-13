#!/usr/bin/env python3
"""Decide whether a pull request event should trigger an automated merge."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


APPROVAL_COMMENT = "同意合并"


def main() -> int:
    event = _load_event(Path(os.environ.get("GITHUB_EVENT_PATH", "")))
    allowed = should_merge(event)
    with _github_output() as output:
        output.write(f"approved={'true' if allowed else 'false'}\n")
    print(f"approved={'true' if allowed else 'false'}")
    return 0


def should_merge(event: dict[str, Any]) -> bool:
    """Return true for an approved review or an exact merge-approval comment."""

    action = str(event.get("action") or "")
    if "review" in event:
        review = event.get("review") or {}
        return action == "submitted" and str(review.get("state") or "").upper() == "APPROVED"

    if "comment" in event:
        comment = event.get("comment") or {}
        issue = event.get("issue") or {}
        return (
            action == "created"
            and "pull_request" in issue
            and str(comment.get("body") or "").strip() == APPROVAL_COMMENT
        )

    return False


def _load_event(path: Path) -> dict[str, Any]:
    if not path:
        return {}
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise SystemExit("GitHub event payload must be a JSON object")
    return payload


class _OutputFile:
    def __enter__(self) -> "_OutputFile":
        output_path = os.environ.get("GITHUB_OUTPUT")
        self._file = open(output_path, "a", encoding="utf-8") if output_path else sys.stdout
        return self

    def write(self, value: str) -> None:
        self._file.write(value)

    def __exit__(self, *_exc: object) -> None:
        if self._file is not sys.stdout:
            self._file.close()


def _github_output() -> _OutputFile:
    return _OutputFile()


if __name__ == "__main__":
    raise SystemExit(main())
