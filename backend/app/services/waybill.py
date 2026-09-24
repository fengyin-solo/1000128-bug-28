"""运单管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "waybill"
REQUIRED_FIELDS = ["运单号", "关联订单", "承运车辆"]
STATUS_ORDER = ["待装车", "运输中", "已签收", "已作废"]
ACTION_RULES = {"确认装车": "运输中", "签收运单": "已签收", "作废运单": "已作废"}
NEGATIVE_ACTIONS = ["作废运单"]

# 动作只有在运单处于对应前置状态时才允许执行，避免签收/作废后的脏回写。
ACTION_PRECONDITIONS = {"确认装车": "待装车", "签收运单": "运输中"}
# 装车前与在途都允许作废；已签收的运单只能走售后流程。
VOIDABLE_STATUSES = ("待装车", "运输中")

# 作废运单后需要一并清理的关联模块及其运单号字段：温控监控、温度异常。
CASCADE_MODULES = [("temperature", "关联运单"), ("excursion", "关联运单")]


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
        entry["司机姓名"] = values.get("司机姓名") or ""
        entry["装车时间"] = ""
        entry["卸货时间"] = ""
        entry["status"] = STATUS_ORDER[0]
        entry["运单状态"] = entry["status"]
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
        required_status = ACTION_PRECONDITIONS.get(action)
        current_status = str(entry.get("status") or "")
        if required_status and current_status != required_status:
            return None, f"运单当前为「{current_status}」，不允许执行「{action}」"
        if action == "作废运单" and current_status not in VOIDABLE_STATUSES:
            return None, f"运单当前为「{current_status}」，不允许执行「{action}」"
        entry["status"] = target
        # 运单状态是给列表/详情共同读取的展示字段，必须与 status 同源同步回写。
        entry["运单状态"] = target
        entry["pending"] = target not in ("已签收", "已作废")
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == "确认装车":
            entry["装车时间"] = _now_text()
        elif action == "签收运单":
            entry["卸货时间"] = _now_text()
        elif action == "作废运单":
            self._clean_related(entry.get("运单号"))
        return entry, f"冷链运单已{action}"

    def _clean_related(self, waybill_no: Any) -> None:
        """作废后清理引用该运单号的温控记录与温度异常事件，避免监控侧残留脏数据。"""
        if not waybill_no:
            return
        for module, ref_field in CASCADE_MODULES:
            table = store.rows(module)
            table[:] = [row for row in table if row.get(ref_field) != waybill_no]
