"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class IcebergMigrateService(BaseModuleService):
    MODULE = "iceberg_migrate"
    ENTITY = "迁移计划"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["计划标识", "源格式", "目标格式", "迁移表名", "迁移范围", "校验方式", "迁移状态", "完成表数"]
    REQUIRED_FIELDS = ["计划标识", "源格式", "目标格式"]
    STATUS_ORDER = ["待执行", "迁移中", "已完成", "迁移失败"]
    ACTION_RULES = {"启动迁移": "待执行", "取消迁移": "迁移中"}
    NEGATIVE_ACTIONS = []
