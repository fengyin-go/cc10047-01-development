"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class StreamQualityService(BaseModuleService):
    MODULE = "stream_quality"
    ENTITY = "质量检查"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["检查标识", "目标管道", "校验字段", "校验算子", "告警阈值", "采样窗口", "阻断级别", "上次结果"]
    REQUIRED_FIELDS = ["检查标识", "目标管道", "校验字段"]
    STATUS_ORDER = ["待启用", "运行中", "已通过", "未通过"]
    ACTION_RULES = {"启动检查": "待启用", "忽略告警": "未通过"}
    NEGATIVE_ACTIONS = ["忽略告警"]
