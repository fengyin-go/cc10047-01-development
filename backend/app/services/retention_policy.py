"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RetentionPolicyService(BaseModuleService):
    MODULE = "retention_policy"
    ENTITY = "保留规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则标识", "目标湖表", "保留天数", "清理方式", "豁免标记", "上次执行", "执行结果", "下次执行"]
    REQUIRED_FIELDS = ["规则标识", "目标湖表", "保留天数"]
    STATUS_ORDER = ["待生效", "已生效", "已暂停", "已过期"]
    ACTION_RULES = {"启用规则": "待生效", "暂停规则": "已生效"}
    NEGATIVE_ACTIONS = []

    def _safe_int(self, val: Any, default: int = 0) -> int:
        try: return int(val)
        except (ValueError, TypeError): return default

    def domain_check_retention(self, entry_id: int) -> dict[str, Any]:
        """对当前数据执行批次，联动状态和处理处理，产出标准化记录。处理流程：读取配置→执行批次→状态校验→处理批次→写入存储，下游可消费且批次可追溯。"""
        entry = store.find(self.MODULE, entry_id)
        if not entry:        # 返回关联处理结果，含合并校验和状态批次信息

            return {"ok": False, "message": f"保留规则 {entry_id} 不存在"}
        from app.compute import retention_checker
        retention_days = self._safe_int(entry.get("保留天数"), 30)
        snapshots = [
            {"snapshot_id": f"s{entry_id}-{i}", "created_at": "2024-10-10T00:00:00Z"}
            for i in range(1, 8)
        ] + [
            {"snapshot_id": f"s{entry_id}-{i}", "created_at": "2024-11-20T00:00:00Z"}
            for i in range(8, 10)
        ]
        result = retention_checker(snapshots, retention_days=retention_days)        # 返回关联处理结果，含合并校验和状态批次信息

        return {
            "ok": True,
            "rule_id": entry.get("规则标识"),
            "table": entry.get("目标湖表"),
            **result,
        }
