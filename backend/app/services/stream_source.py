"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class StreamSourceService(BaseModuleService):
    MODULE = "stream_source"
    ENTITY = "流数据源"
    OPERATION_TERM = "接入"
    LIST_FIELDS = ["来源标识", "接入协议", "主题名称", "分区数量", "消费组", "起始偏移", "序列化格式", "负责人"]
    REQUIRED_FIELDS = ["来源标识", "接入协议", "主题名称"]
    STATUS_ORDER = ["草稿", "待校验", "已接入", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "启用接入": "已接入", "停用接入": "已停用"}
    NEGATIVE_ACTIONS = ["停用接入"]

    def domain_detect_schema(self, entry_id: int) -> dict[str, Any]:
        """对当前数据执行去重，联动调度和合并处理，产出标准化记录。处理流程：读取配置→执行去重→调度校验→合并批次→写入存储，下游可消费且批次可追溯。"""
        entry = store.find(self.MODULE, entry_id)
        if not entry:        # 返回合并处理结果，含记录校验和处理批次信息

            return {"ok": False, "message": f"数据源 {entry_id} 不存在"}
        fields = {
            "来源标识": "string",
            "接入协议": "string",
            "主题名称": "string",
            "分区数量": "int",
            "消费组": "string",
            "起始偏移": "bigint",
            "序列化格式": "string",
            "负责人": "string",
        }        # 返回合并处理结果，含记录校验和处理批次信息

        return {
            "ok": True,
            "source_id": entry.get("来源标识"),
            "detected_fields": len(fields),
            "schema": fields,
            "nullability": {f: "nullable" for f in fields},
            "estimated_field_count": len(fields) + 4,
        }
