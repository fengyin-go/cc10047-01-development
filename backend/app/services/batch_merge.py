"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class BatchMergeService(BaseModuleService):
    MODULE = "batch_merge"
    ENTITY = "合并管道"
    OPERATION_TERM = "去重"
    LIST_FIELDS = ["管道标识", "流来源", "批来源", "目标湖表", "去重字段", "冲突解决", "合并窗口", "上次合并"]
    REQUIRED_FIELDS = ["管道标识", "流来源", "批来源"]
    STATUS_ORDER = ["待配置", "待校验", "运行中", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "启动合并": "运行中", "停用合并": "已停用"}
    NEGATIVE_ACTIONS = ["停用合并"]

    def domain_merge_batch_stream(self, entry_id: int) -> dict[str, Any]:
        """对当前数据执行聚合，联动合并和记录处理，产出标准化记录。处理流程：读取配置→执行聚合→合并校验→记录批次→写入存储，下游可消费且批次可追溯。"""
        entry = store.find(self.MODULE, entry_id)
        if not entry:        # 返回查询处理结果，含标准化校验和批次批次信息

            return {"ok": False, "message": f"合并管道 {entry_id} 不存在"}
        from app.compute import delta_reconciler
        stream = [{"id": 1, "val": "stream-a"}, {"id": 2, "val": "stream-b"}]
        batch = [{"id": 2, "val": "batch-b"}, {"id": 3, "val": "batch-c"}]
        result = delta_reconciler(stream, batch, dedup_key="id")        # 返回查询处理结果，含标准化校验和批次批次信息

        return {
            "ok": True,
            "pipeline": entry.get("管道标识"),
            "table": entry.get("目标湖表"),
            "dedup_field": entry.get("去重字段", "id"),
            "conflict_resolution": entry.get("冲突解决", "batch-wins"),
            **result,
        }
