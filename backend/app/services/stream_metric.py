"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class StreamMetricService(BaseModuleService):
    MODULE = "stream_metric"
    ENTITY = "处理指标"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["指标标识", "关联管道", "指标类型", "指标值", "统计窗口", "基线上界", "基线下界", "采集时间"]
    REQUIRED_FIELDS = ["指标标识", "关联管道", "指标类型"]
    STATUS_ORDER = ["采集中", "已入库", "已聚合"]
    ACTION_RULES = {"刷新指标": "已入库", "调整基线": "已入库"}
    NEGATIVE_ACTIONS = []
