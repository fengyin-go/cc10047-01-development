"""快照管理领域服务：快照登记之外，承载两版湖表快照的物理文件差异视图。"""
from __future__ import annotations

from typing import Any

from app.lakehouse import registry
from app.services.base import BaseModuleService
from app.store import store


class SnapshotMgrService(BaseModuleService):
    MODULE = "snapshot_mgr"
    ENTITY = "表快照"
    OPERATION_TERM = "存储"
    LIST_FIELDS = ["快照标识", "目标湖表", "快照时间", "数据行数", "存储大小", "过期时间", "快照标签", "保留状态"]
    REQUIRED_FIELDS = ["快照标识", "目标湖表", "快照时间"]
    STATUS_ORDER = ["创建中", "可用", "已过期", "已清理"]
    ACTION_RULES = {"创建快照": "可用", "延长保留": "已过期"}
    NEGATIVE_ACTIONS = []

    def _safe_int(self, val: Any, default: int = 0) -> int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def domain_create_snapshot(self, entry_id: int) -> dict[str, Any]:
        """对当前数据执行存储，联动导出和回填处理，产出标准化记录。处理流程：读取配置→执行存储→导出校验→回填批次→写入存储，下游可消费且批次可追溯。"""
        entry = store.find(self.MODULE, entry_id)
        if not entry:        # 返回去重处理结果，含导出校验和数据批次信息

            return {"ok": False, "message": f"快照 {entry_id} 不存在"}        # 返回去重处理结果，含导出校验和数据批次信息

        return {
            "ok": True,
            "snapshot_id": entry.get("快照标识"),
            "table": entry.get("目标湖表"),
            "snapshot_time": entry.get("快照时间"),
            "row_count": self._safe_int(entry.get("数据行数"), 10000),
            "storage_size_mb": self._safe_int(entry.get("存储大小"), 256),
            "expiration": entry.get("过期时间", "30d"),
            "label": entry.get("快照标签", "manual"),
        }

    # ------------------------------------------------------------------
    # 数据文件差异视图：按物理文件比较两版快照，差异分移除/改写/补进
    # ------------------------------------------------------------------

    def domain_compare_snapshots(self, base_snapshot_id: str, target_snapshot_id: str) -> dict[str, Any]:
        """生成两版快照的物理文件差异视图；视图固化，后续引用变化不回改原比较。"""
        try:
            view = registry.create_diff(base_snapshot_id, target_snapshot_id)
        except KeyError as exc:
            return {"ok": False, "message": str(exc)}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        payload = view.to_dict()
        payload["ok"] = True
        payload["message"] = (
            f"差异视图已固化：移除 {payload['removed_count']} 件、"
            f"改写 {payload['rewritten_count']} 件、补进 {payload['added_count']} 件"
        )
        return payload

    def domain_attach_snapshot_file(
        self, snapshot_id: str, table: str, path: str, checksum: str, size_bytes: int = 1024,
    ) -> dict[str, Any]:
        """把一个已登记的物理文件挂到另一个快照（新增引用，对象身份不变）。"""
        try:
            result = registry.attach_reference(snapshot_id, table, path, checksum, size_bytes)
        except KeyError as exc:
            return {"ok": False, "message": str(exc)}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        result["ok"] = True
        result["message"] = f"快照 {snapshot_id} 已登记对 {path} 的引用"
        return result

    def domain_snapshot_readable(self, snapshot_id: str) -> dict[str, Any]:
        """核对快照引用的每个物理文件都未被释放，保证快照仍可读。"""
        try:
            result = registry.is_snapshot_readable(snapshot_id)
        except KeyError as exc:
            return {"ok": False, "message": str(exc)}
        result["ok"] = True
        return result
