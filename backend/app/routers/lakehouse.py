"""湖表快照数据文件差异视图、回收复核与历史清理专用路由。

与通用资源路由 /api/{module} 平级，且必须先于其注册，
否则 /api/lakehouse/... 会被动态模块路由抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.lakehouse import ReceiptSaveInterrupted, registry

router = APIRouter(prefix="/api/lakehouse", tags=["湖表文件差异与回收"])


class DiffPayload(BaseModel):
    base_snapshot_id: str
    target_snapshot_id: str


class AttachPayload(BaseModel):
    table: str
    path: str
    checksum: str
    size_bytes: int = 1024


class ReviewPayload(BaseModel):
    diff_id: str
    operator: str = "值班员"


class VacuumPayload(BaseModel):
    review_id: str
    operator: str = "值班员"
    # 处理够 N 件后模拟保存回执中断；None 表示正常执行到底
    fail_after: int | None = Field(default=None, ge=1)


# ----- 快照与引用 -----

@router.get("/snapshots")
def list_snapshots() -> dict[str, Any]:
    return {"items": registry.list_snapshots()}


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str) -> dict[str, Any]:
    snapshot = registry.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"快照 {snapshot_id} 不存在")
    return snapshot


@router.post("/snapshots/{snapshot_id}/attach")
def attach_snapshot_file(snapshot_id: str, payload: AttachPayload) -> dict[str, Any]:
    try:
        return registry.attach_reference(
            snapshot_id, payload.table, payload.path, payload.checksum, payload.size_bytes,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/snapshots/{snapshot_id}/readable")
def snapshot_readable(snapshot_id: str) -> dict[str, Any]:
    try:
        return registry.is_snapshot_readable(snapshot_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ----- 数据文件差异视图 -----

@router.post("/diffs")
def create_diff(payload: DiffPayload) -> dict[str, Any]:
    try:
        view = registry.create_diff(payload.base_snapshot_id, payload.target_snapshot_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return view.to_dict()


@router.get("/diffs")
def list_diffs() -> dict[str, Any]:
    return {"items": registry.list_diffs()}


@router.get("/diffs/{diff_id}")
def get_diff(diff_id: str) -> dict[str, Any]:
    view = registry.get_diff(diff_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"差异视图 {diff_id} 不存在")
    return view


# ----- 回收复核 -----

@router.post("/reviews")
def create_review(payload: ReviewPayload) -> dict[str, Any]:
    try:
        review = registry.create_review(payload.diff_id, payload.operator)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return review.to_dict()


@router.get("/reviews")
def list_reviews() -> dict[str, Any]:
    return {"items": registry.list_reviews()}


@router.get("/reviews/{review_id}")
def get_review(review_id: str) -> dict[str, Any]:
    review = registry.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"回收复核单 {review_id} 不存在")
    return review


@router.get("/reviews/{review_id}/next-scope")
def next_review_scope(review_id: str) -> dict[str, Any]:
    try:
        return registry.next_review_scope(review_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ----- 历史清理执行 -----

@router.post("/vacuum")
def run_vacuum(payload: VacuumPayload) -> dict[str, Any]:
    try:
        receipt = registry.execute_vacuum(
            payload.review_id, payload.operator, fail_after=payload.fail_after,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReceiptSaveInterrupted as exc:
        # 处置清单已逐件落盘，回执保持中断态，前端可用同一复核单重试接续
        partial = next(
            (r for r in registry.list_receipts(payload.review_id) if r["status"] == "中断"),
            None,
        )
        return {
            "ok": False,
            "interrupted": True,
            "message": str(exc),
            "receipt": partial,
        }
    return {"ok": True, "interrupted": False, "receipt": receipt}


@router.get("/receipts")
def list_receipts(review_id: str | None = None) -> dict[str, Any]:
    return {"items": registry.list_receipts(review_id)}


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: str) -> dict[str, Any]:
    receipt = registry.get_receipt(receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"执行回执 {receipt_id} 不存在")
    return receipt


# ----- 文件处置清单 -----

@router.get("/ledger")
def list_ledger(
    review_id: str | None = None,
    object_id: str | None = None,
    actually_released: bool | None = None,
) -> dict[str, Any]:
    rows = registry.list_ledger(
        review_id=review_id, object_id=object_id, actually_released=actually_released,
    )
    return {"items": rows, "total": len(rows)}


# ----- 文件对象身份查询 -----

@router.get("/objects/lookup")
def lookup_object(table: str, path: str) -> dict[str, Any]:
    obj = registry.lookup_object(table, path)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"湖表 {table} 下的文件 {path} 未登记")
    return obj


@router.get("/objects/{object_id}")
def get_object(object_id: str) -> dict[str, Any]:
    obj = registry.get_object(object_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"文件对象 {object_id} 不存在")
    return obj
