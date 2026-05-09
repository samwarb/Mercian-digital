#!/usr/bin/env python3
"""Build a daily monday.com report with 'Do Today' and 'Completed Yesterday'."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib import error, request


API_URL = "https://api.monday.com/v2"

DONE_WORDS = {
    "done",
    "complete",
    "completed",
    "closed",
    "finished",
    "resolved",
    "shipped",
    "delivered",
    "approved",
}
BLOCKED_WORDS = {
    "blocked",
    "stuck",
    "waiting",
    "on hold",
    "hold",
    "dependency",
    "dependencies",
}
STATUS_COLUMN_HINTS = {"status", "state", "workflow", "progress"}
DUE_COLUMN_HINTS = {"due", "deadline", "target", "date"}


class MondayAccessError(RuntimeError):
    """Raised when monday.com access is incomplete or unavailable."""


@dataclass
class ColumnDefinition:
    id: str
    title: str
    type: str
    settings: Dict[str, Any]


@dataclass
class TaskRecord:
    board_id: str
    board_name: str
    item_id: str
    item_name: str
    item_url: Optional[str]
    group_name: Optional[str]
    status_text: Optional[str]
    due_date: Optional[date]
    blocked: bool
    overdue: bool
    due_today: bool


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise MondayAccessError(
            f"Missing access: {name} environment variable is not set."
        )
    return value


def parse_board_ids(raw_value: str) -> List[int]:
    board_ids: List[int] = []
    for chunk in raw_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.isdigit():
            raise MondayAccessError(
                "Missing access: MONDAY_BOARD_IDS must be a comma-separated list of "
                f"numeric board IDs, but '{chunk}' is not numeric."
            )
        board_ids.append(int(chunk))
    if not board_ids:
        raise MondayAccessError(
            "Missing access: MONDAY_BOARD_IDS environment variable is set but empty."
        )
    return board_ids


def monday_request(
    token: str, query: str, variables: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
            "API-Version": "2024-10",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in {401, 403}:
            raise MondayAccessError(
                f"Missing access: monday.com API returned HTTP {exc.code}. "
                "Check whether MONDAY_API_TOKEN is valid and allowed to access the "
                "requested boards."
            ) from exc
        raise MondayAccessError(
            f"Missing access: monday.com API request failed with HTTP {exc.code}: {body}"
        ) from exc
    except error.URLError as exc:
        raise MondayAccessError(
            f"Missing access: unable to reach monday.com API at {API_URL}: {exc.reason}"
        ) from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MondayAccessError(
            f"Missing access: monday.com API returned non-JSON content: {body[:200]}"
        ) from exc

    errors_payload = data.get("errors") or []
    if errors_payload:
        message = "; ".join(error_obj.get("message", "Unknown error") for error_obj in errors_payload)
        lowered = message.lower()
        if "auth" in lowered or "permission" in lowered or "access denied" in lowered:
            raise MondayAccessError(
                f"Missing access: monday.com API rejected the request: {message}"
            )
        raise MondayAccessError(f"Missing access: monday.com API returned errors: {message}")
    return data


def parse_iso_date(raw_value: Optional[str]) -> Optional[date]:
    if not raw_value:
        return None
    candidate = raw_value[:10]
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


def parse_timestamp(raw_value: Optional[str]) -> Optional[datetime]:
    if not raw_value:
        return None
    normalized = raw_value.replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def monday_timezone() -> timezone:
    offset_hours = int(os.getenv("MONDAY_TIMEZONE_OFFSET_HOURS", "0"))
    return timezone(timedelta(hours=offset_hours))


def local_day_window(target_day: date, tz: timezone) -> Tuple[datetime, datetime]:
    start = datetime.combine(target_day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def choose_status_column(columns: Sequence[ColumnDefinition]) -> Optional[ColumnDefinition]:
    status_columns = [column for column in columns if column.type == "status"]
    if not status_columns:
        return None
    preferred = [
        column
        for column in status_columns
        if any(hint in f"{column.id} {column.title}".lower() for hint in STATUS_COLUMN_HINTS)
    ]
    return preferred[0] if preferred else status_columns[0]


def choose_due_columns(columns: Sequence[ColumnDefinition]) -> List[ColumnDefinition]:
    due_types = {"date", "timeline"}
    candidates = [column for column in columns if column.type in due_types]
    hinted = [
        column
        for column in candidates
        if any(hint in f"{column.id} {column.title}".lower() for hint in DUE_COLUMN_HINTS)
    ]
    return hinted or candidates


def settings_to_labels(column: ColumnDefinition) -> Dict[str, str]:
    labels = column.settings.get("labels") or {}
    return {str(key): value for key, value in labels.items()}


def read_status_text(
    item: Dict[str, Any], status_column: Optional[ColumnDefinition]
) -> Optional[str]:
    if not status_column:
        return None
    for column_value in item.get("column_values", []):
        if column_value.get("id") != status_column.id:
            continue
        text = (column_value.get("text") or "").strip()
        if text:
            return text
        raw_value = column_value.get("value")
        if not raw_value:
            return None
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return None
        label = parsed.get("label")
        if label:
            return str(label)
        index = parsed.get("index")
        if index is None:
            return None
        return settings_to_labels(status_column).get(str(index))
    return None


def read_due_date(item: Dict[str, Any], due_columns: Sequence[ColumnDefinition]) -> Optional[date]:
    due_column_ids = {column.id for column in due_columns}
    for column_value in item.get("column_values", []):
        if column_value.get("id") not in due_column_ids:
            continue
        raw_value = column_value.get("value")
        if raw_value:
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                parsed = {}
            if "date" in parsed:
                parsed_date = parse_iso_date(parsed.get("date"))
                if parsed_date:
                    return parsed_date
            if "to" in parsed:
                parsed_date = parse_iso_date(parsed.get("to"))
                if parsed_date:
                    return parsed_date
            if "from" in parsed:
                parsed_date = parse_iso_date(parsed.get("from"))
                if parsed_date:
                    return parsed_date
        parsed_date = parse_iso_date(column_value.get("text"))
        if parsed_date:
            return parsed_date
    return None


def is_done_status(status_text: Optional[str]) -> bool:
    if not status_text:
        return False
    normalized = status_text.strip().lower()
    return normalized in DONE_WORDS or any(word in normalized for word in DONE_WORDS)


def is_blocked_status(status_text: Optional[str]) -> bool:
    if not status_text:
        return False
    normalized = status_text.strip().lower()
    return normalized in BLOCKED_WORDS or any(word in normalized for word in BLOCKED_WORDS)


def build_task_records(
    board: Dict[str, Any], columns: Sequence[ColumnDefinition], today: date
) -> List[TaskRecord]:
    status_column = choose_status_column(columns)
    due_columns = choose_due_columns(columns)
    records: List[TaskRecord] = []
    for item in board.get("items", []):
        status_text = read_status_text(item, status_column)
        due_date = read_due_date(item, due_columns)
        blocked = is_blocked_status(status_text)
        overdue = bool(due_date and due_date < today and not is_done_status(status_text))
        due_today = bool(due_date and due_date == today and not is_done_status(status_text))
        records.append(
            TaskRecord(
                board_id=str(board["id"]),
                board_name=board["name"],
                item_id=str(item["id"]),
                item_name=item["name"],
                item_url=item.get("url"),
                group_name=(item.get("group") or {}).get("title"),
                status_text=status_text,
                due_date=due_date,
                blocked=blocked,
                overdue=overdue,
                due_today=due_today,
            )
        )
    return records


def format_task(record: TaskRecord) -> str:
    flags: List[str] = []
    if record.overdue:
        flags.append("OVERDUE")
    if record.due_today:
        flags.append("DUE TODAY")
    if record.blocked:
        flags.append("BLOCKED")

    parts = [record.item_name]
    if record.status_text:
        parts.append(f"status: {record.status_text}")
    if record.due_date:
        parts.append(f"due: {record.due_date.isoformat()}")
    if record.group_name:
        parts.append(f"group: {record.group_name}")
    parts.append(f"board: {record.board_name}")
    if flags:
        parts.append("flags: " + ", ".join(flags))
    if record.item_url:
        parts.append(f"url: {record.item_url}")
    return " - ".join(parts)


def parse_log_data(raw_value: Any) -> Dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if isinstance(raw_value, str) and raw_value:
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def possible_done_change(log_entry: Dict[str, Any]) -> bool:
    haystacks: List[str] = []
    for key in ("event", "text"):
        value = log_entry.get(key)
        if isinstance(value, str):
            haystacks.append(value.lower())
    data = parse_log_data(log_entry.get("data"))
    for value in data.values():
        if isinstance(value, str):
            haystacks.append(value.lower())
        elif isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, str):
                    haystacks.append(nested.lower())
    combined = " ".join(haystacks)
    if "status" not in combined and "label" not in combined and "done" not in combined:
        return False
    return any(done_word in combined for done_word in DONE_WORDS)


def fetch_board_snapshot(token: str, board_id: int) -> Tuple[Dict[str, Any], List[ColumnDefinition]]:
    query = """
    query BoardSnapshot($boardId: [ID!]) {
      boards(ids: $boardId) {
        id
        name
        columns {
          id
          title
          type
          settings_str
        }
        items_page(limit: 500) {
          items {
            id
            name
            url
            group {
              title
            }
            column_values {
              id
              text
              value
              type
            }
          }
        }
      }
    }
    """
    response = monday_request(token, query, {"boardId": [board_id]})
    boards = response.get("data", {}).get("boards") or []
    if not boards:
        raise MondayAccessError(
            f"Missing access: board {board_id} was not returned by monday.com. "
            "The token may not have access to that board, or the board ID is invalid."
        )
    board = boards[0]
    columns = []
    for raw_column in board.get("columns", []):
        settings_raw = raw_column.get("settings_str") or "{}"
        try:
            settings = json.loads(settings_raw)
        except json.JSONDecodeError:
            settings = {}
        columns.append(
            ColumnDefinition(
                id=raw_column["id"],
                title=raw_column["title"],
                type=raw_column["type"],
                settings=settings,
            )
        )
    flattened_board = {
        "id": board["id"],
        "name": board["name"],
        "items": board.get("items_page", {}).get("items", []),
    }
    return flattened_board, columns


def fetch_completed_yesterday(
    token: str, board_id: int, board_name: str, yesterday: date, tz: timezone
) -> List[str]:
    start, end = local_day_window(yesterday, tz)
    query = """
    query BoardActivity($boardId: [ID!], $from: ISO8601DateTime!, $to: ISO8601DateTime!) {
      boards(ids: $boardId) {
        activity_logs(from: $from, to: $to, limit: 200) {
          id
          event
          created_at
          data
          entity
        }
      }
    }
    """
    response = monday_request(
        token,
        query,
        {
            "boardId": [board_id],
            "from": start.isoformat(),
            "to": end.isoformat(),
        },
    )
    boards = response.get("data", {}).get("boards") or []
    if not boards:
        return []

    completed: List[str] = []
    seen_items: Set[str] = set()
    for log_entry in boards[0].get("activity_logs", []):
        created_at = parse_timestamp(log_entry.get("created_at"))
        if created_at is None or not (start <= created_at.astimezone(tz) < end):
            continue
        if not possible_done_change(log_entry):
            continue

        entity = log_entry.get("entity")
        item_id = None
        item_name = None
        if isinstance(entity, dict):
            item_id = entity.get("id")
            item_name = entity.get("name")
        elif isinstance(entity, str):
            item_name = entity

        data = parse_log_data(log_entry.get("data"))
        item_name = (
            item_name
            or data.get("pulseName")
            or data.get("itemName")
            or data.get("name")
            or "Unknown item"
        )
        dedupe_key = str(item_id or item_name)
        if dedupe_key in seen_items:
            continue
        seen_items.add(dedupe_key)
        completed.append(f"{item_name} - board: {board_name} - changed to done on {yesterday.isoformat()}")
    return completed


def render_report(do_today: Sequence[TaskRecord], completed_yesterday: Sequence[str]) -> str:
    lines = ["Do Today"]
    if do_today:
        for record in do_today:
            lines.append(f"- {format_task(record)}")
    else:
        lines.append("- No open items matched the due today, overdue, or blocked filters.")

    lines.append("")
    lines.append("Completed Yesterday")
    if completed_yesterday:
        for entry in completed_yesterday:
            lines.append(f"- {entry}")
    else:
        lines.append("- No tasks were detected as changing to done yesterday.")
    return "\n".join(lines)


def sort_do_today(records: Iterable[TaskRecord]) -> List[TaskRecord]:
    return sorted(
        records,
        key=lambda record: (
            0 if record.overdue else 1,
            0 if record.due_today else 1,
            0 if record.blocked else 1,
            record.due_date or date.max,
            record.board_name.lower(),
            record.item_name.lower(),
        ),
    )


def main() -> int:
    try:
        token = env_required("MONDAY_API_TOKEN")
        board_ids = parse_board_ids(env_required("MONDAY_BOARD_IDS"))
        tz = monday_timezone()
        today = datetime.now(tz).date()
        yesterday = today - timedelta(days=1)

        do_today: List[TaskRecord] = []
        completed_yesterday: List[str] = []
        for board_id in board_ids:
            board, columns = fetch_board_snapshot(token, board_id)
            task_records = build_task_records(board, columns, today)
            do_today.extend(
                record
                for record in task_records
                if not is_done_status(record.status_text)
                and (record.overdue or record.due_today or record.blocked)
            )
            completed_yesterday.extend(
                fetch_completed_yesterday(token, board_id, board["name"], yesterday, tz)
            )

        print(render_report(sort_do_today(do_today), completed_yesterday))
        return 0
    except MondayAccessError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
