"""调度派单业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "dispatch"
REQUIRED_FIELDS = ["调度单号", "关联订单", "配送线路"]
STATUS_ORDER = ["待派单", "已派单", "已发车", "已撤销"]
ACTION_RULES = {"确认派单": "已派单", "确认发车": "已发车", "撤销派单": "已撤销"}
NEGATIVE_ACTIONS = ["撤销派单"]
ACTION_PRECONDITIONS = {"确认派单": "待派单", "确认发车": "已派单", "撤销派单": "已派单"}


class DispatchService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("调度单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["指派车辆"] = ""
        entry["指派司机"] = ""
        entry["计划发车时间"] = values.get("计划发车时间") or ""
        entry["status"] = STATUS_ORDER[0]
        entry["调度状态"] = entry["status"]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"调度单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于调度派单可执行范围"
        target = ACTION_RULES[action]
        required_status = ACTION_PRECONDITIONS.get(action)
        current_status = str(entry.get("status") or "")
        if required_status and current_status != required_status:
            return None, f"调度单当前为「{current_status}」，不允许执行「{action}」"
        entry["status"] = target
        entry["调度状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == "撤销派单":
            # 收回派单：清空上一张单残留的指派车辆/司机，释放后才能重新指派。
            entry["指派车辆"] = ""
            entry["指派司机"] = ""
        return entry, f"调度单已{action}"
