"""运单管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "waybill"
REQUIRED_FIELDS = ["运单号", "关联订单", "承运车辆"]
STATUS_ORDER = ["待装车", "运输中", "已签收", "已作废"]
TERMINAL_STATUSES = {"已签收", "已作废"}
ACTION_RULES = {"确认装车": "运输中", "签收运单": "已签收", "作废运单": "已作废"}
NEGATIVE_ACTIONS = ["作废运单"]
# 动作发生后要回写的时间字段：装车与签收都以动作发生的时刻为准。
ACTION_TIME_FIELDS = {"确认装车": "装车时间", "签收运单": "卸货时间"}


class WaybillService:
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
            rows = [row for row in rows if keyword in str(row.get("运单号", ""))]
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
        entry["status"] = STATUS_ORDER[0]
        entry["运单状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"冷链运单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于运单管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["运单状态"] = target
        entry["pending"] = target not in TERMINAL_STATUSES
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        time_field = ACTION_TIME_FIELDS.get(action)
        if time_field:
            entry[time_field] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"冷链运单已{action}"
        if action == "作废运单":
            released, removed = self._cleanup_links(entry)
            message += f"，已释放 {released} 张调度单的车辆指派，清理 {removed} 条温控记录"
        return entry, message

    def _cleanup_links(self, entry: dict[str, Any]) -> tuple[int, int]:
        """作废后的关联清理：调度单释放车辆与司机，温控记录随运单一并下架。"""
        order_no = str(entry.get("关联订单") or "").strip()
        vehicle = str(entry.get("承运车辆") or "").strip()
        released = 0
        for row in store.rows("dispatch"):
            linked = (order_no and str(row.get("关联订单") or "").strip() == order_no) or (
                vehicle and str(row.get("指派车辆") or "").strip() == vehicle
            )
            if not linked:
                continue
            row["指派车辆"] = ""
            row["指派司机"] = ""
            row["status"] = "待派单"
            row["调度状态"] = "待派单"
            row["pending"] = True
            released += 1
        waybill_no = str(entry.get("运单号") or "").strip()
        removed = 0
        if waybill_no:
            rows = store.rows("temperature")
            kept = [row for row in rows if str(row.get("关联运单") or "").strip() != waybill_no]
            removed = len(rows) - len(kept)
            rows[:] = kept
        return released, removed
