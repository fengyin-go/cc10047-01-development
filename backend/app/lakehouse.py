"""湖表快照数据文件差异与回收复核领域引擎。

核心约束：
1. 数据文件差异按物理文件（湖表 + 物理路径确定的对象身份）比较，
   差异视图一旦生成即固化，后续快照引用变化不回改原比较。
2. 回收复核从差异视图的移除项（含改写前的旧对象）发起，
   清理执行时重新核对当前全部快照引用，仍被引用的对象只登记不释放。
3. 释放按对象身份幂等：多个值班员并发/重复提交同一份复核，
   每个物理对象至多释放一次，处置清单逐件记录原快照版本、文件身份和真实释放结果。
4. 不同湖表中的同名路径是彼此独立的文件对象。
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# 领域对象
# ---------------------------------------------------------------------------

@dataclass
class FileObject:
    """物理数据文件对象，身份由 (湖表, 物理路径) 唯一确定。"""

    object_id: str
    table: str
    path: str
    size_bytes: int
    checksum: str
    released: bool = False
    released_at: str | None = None
    released_by_receipt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "table": self.table,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "released": self.released,
            "released_at": self.released_at,
            "released_by_receipt": self.released_by_receipt,
        }


@dataclass
class DiffItem:
    """差异视图中的一条文件记录，固化生成时刻的对象身份。"""

    category: str  # removed 移除 / rewritten 改写 / added 补进
    object_id: str
    table: str
    path: str
    checksum: str
    size_bytes: int
    origin_snapshot_id: str
    paired_object_id: str | None = None  # 改写项指向新对象（旧→新）

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "object_id": self.object_id,
            "table": self.table,
            "path": self.path,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "origin_snapshot_id": self.origin_snapshot_id,
            "paired_object_id": self.paired_object_id,
        }


@dataclass
class DiffView:
    """两版快照按物理文件计算的差异视图，生成后不可变。"""

    diff_id: str
    table: str
    base_snapshot_id: str
    target_snapshot_id: str
    created_at: str
    removed: list[DiffItem]
    rewritten: list[DiffItem]
    added: list[DiffItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "diff_id": self.diff_id,
            "table": self.table,
            "base_snapshot_id": self.base_snapshot_id,
            "target_snapshot_id": self.target_snapshot_id,
            "created_at": self.created_at,
            "removed": [item.to_dict() for item in self.removed],
            "rewritten": [item.to_dict() for item in self.rewritten],
            "added": [item.to_dict() for item in self.added],
            "removed_count": len(self.removed),
            "rewritten_count": len(self.rewritten),
            "added_count": len(self.added),
            "frozen": True,
        }


@dataclass
class ReviewItem:
    """回收复核范围内的一个候选文件。"""

    object_id: str
    table: str
    path: str
    checksum: str
    origin_snapshot_id: str
    source_category: str  # removed / rewritten-old

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "table": self.table,
            "path": self.path,
            "checksum": self.checksum,
            "origin_snapshot_id": self.origin_snapshot_id,
            "source_category": self.source_category,
        }


@dataclass
class Review:
    """从差异视图发起的回收复核单，承载固定的复核范围。"""

    review_id: str
    diff_id: str
    table: str
    base_snapshot_id: str
    created_at: str
    operator: str
    items: list[ReviewItem]
    status: str = "待复核"  # 待复核 / 清理中 / 已完成 / 中断
    receipt_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "diff_id": self.diff_id,
            "table": self.table,
            "base_snapshot_id": self.base_snapshot_id,
            "created_at": self.created_at,
            "operator": self.operator,
            "status": self.status,
            "receipt_ids": list(self.receipt_ids),
            "items": [item.to_dict() for item in self.items],
            "item_count": len(self.items),
        }


class ReceiptSaveInterrupted(RuntimeError):
    """保存执行回执过程中发生中断，已落盘的逐件处置记录保持有效。"""


# ---------------------------------------------------------------------------
# 注册表与领域服务
# ---------------------------------------------------------------------------

class LakehouseRegistry:
    """内存态湖表文件注册表：快照引用、差异视图、复核单、回执与处置清单。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._objects: dict[str, FileObject] = {}
        self._object_index: dict[tuple[str, str], str] = {}
        # snapshot_id -> {"table", "label", "objects": [object_id...]}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._diffs: dict[str, DiffView] = {}
        self._reviews: dict[str, Review] = {}
        self._receipts: dict[str, dict[str, Any]] = {}
        # 文件处置清单（逐件、仅追加）
        self._ledger: list[dict[str, Any]] = []
        self._seq = 0
        self._seed_demo()

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ----- 初始化演示数据：S1/S2/S3 与跨表同名文件 -----

    def _seed_demo(self) -> None:
        table = "lakehouse_demo"
        # S1 持有 p、q、r、u(旧版)
        s1_files = [
            ("p.parquet", "ck-p-1", 1024),
            ("q.parquet", "ck-q-1", 2048),
            ("r.parquet", "ck-r-1", 4096),
            ("u.parquet", "ck-u-1", 512),
        ]
        s1_ids = [self._register_object(table, path, ck, size) for path, ck, size in s1_files]
        self._snapshots["S1"] = {"table": table, "label": "快照S1", "objects": s1_ids}

        # S2 持有 r(不变)、u(改写后)、s(补进)；p、q 被移除
        s2_specs = [
            ("r.parquet", "ck-r-1", 4096),
            ("u.parquet", "ck-u-2", 640),
            ("s.parquet", "ck-s-1", 3072),
        ]
        s2_ids = [self._register_object(table, path, ck, size) for path, ck, size in s2_specs]
        self._snapshots["S2"] = {"table": table, "label": "快照S2", "objects": s2_ids}

        # S3 初始为空快照，等待后续登记引用
        self._snapshots["S3"] = {"table": table, "label": "快照S3", "objects": []}

        # 另一张湖表登记同名 p.parquet，验证跨表独立身份
        other = "lakehouse_other"
        other_p = self._register_object(other, "p.parquet", "ck-other-p-1", 999)
        self._snapshots["S9"] = {"table": other, "label": "其他湖表快照", "objects": [other_p]}

    def _register_object(self, table: str, path: str, checksum: str, size_bytes: int) -> str:
        key = (table, path)
        existing = self._object_index.get(key)
        if existing is not None:
            obj = self._objects[existing]
            if obj.checksum == checksum:
                return existing
            # 同表同路径但校验和不同：视为改写产生的新物理对象，分配独立身份
            object_id = self._next_id("obj")
        else:
            object_id = self._next_id("obj")
        self._objects[object_id] = FileObject(
            object_id=object_id,
            table=table,
            path=path,
            size_bytes=size_bytes,
            checksum=checksum,
        )
        self._object_index[key] = object_id
        return object_id

    # ----- 快照与引用 -----

    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for snapshot_id, snap in self._snapshots.items():
                files = [
                    self._objects[oid].to_dict()
                    for oid in snap["objects"]
                    if oid in self._objects
                ]
                result.append({
                    "snapshot_id": snapshot_id,
                    "table": snap["table"],
                    "label": snap["label"],
                    "files": files,
                    "file_count": len(files),
                })
            return sorted(result, key=lambda item: item["snapshot_id"])

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._lock:
            snap = self._snapshots.get(snapshot_id)
            if snap is None:
                return None
            files = [self._objects[oid].to_dict() for oid in snap["objects"]]
            return {
                "snapshot_id": snapshot_id,
                "table": snap["table"],
                "label": snap["label"],
                "files": files,
                "file_count": len(files),
            }

    def attach_reference(
        self, snapshot_id: str, table: str, path: str, checksum: str, size_bytes: int = 1024,
    ) -> dict[str, Any]:
        """把一个已存在的物理文件登记到指定快照（新增引用，不复制对象身份）。"""
        with self._lock:
            snap = self._snapshots.get(snapshot_id)
            if snap is None:
                raise KeyError(f"快照 {snapshot_id} 不存在")
            object_id = self._object_index.get((table, path))
            if object_id is None:
                raise KeyError(f"湖表 {table} 下不存在物理文件 {path}，无可登记对象")
            obj = self._objects[object_id]
            if obj.checksum != checksum:
                raise ValueError("登记校验和与现存物理文件不一致，拒绝挂接")
            if object_id not in snap["objects"]:
                snap["objects"].append(object_id)
            return {"snapshot_id": snapshot_id, "file": obj.to_dict(), "attached": True}

    def _snapshots_referring(self, object_id: str, *, exclude: str | None = None) -> list[str]:
        """实时计算当前仍引用该对象的全部快照（执行时刻核对，不用差异时刻的旧范围）。

        exclude 为差异比较的基准快照：其引用属于本差异已替换/移除的旧范围，
        不计入活动引用；基准之外的快照仍引用时必须保住对象。
        """
        return [
            snapshot_id
            for snapshot_id, snap in self._snapshots.items()
            if object_id in snap["objects"] and snapshot_id != exclude
        ]

    def is_snapshot_readable(self, snapshot_id: str) -> dict[str, Any]:
        """核对快照可读性：其引用的每个对象都未被物理释放。"""
        with self._lock:
            snap = self._snapshots.get(snapshot_id)
            if snap is None:
                raise KeyError(f"快照 {snapshot_id} 不存在")
            unreadable = []
            for object_id in snap["objects"]:
                obj = self._objects.get(object_id)
                if obj is None or obj.released:
                    unreadable.append(object_id)
            return {
                "snapshot_id": snapshot_id,
                "readable": not unreadable,
                "missing_or_released": unreadable,
                "referenced_files": [self._objects[oid].to_dict() for oid in snap["objects"]],
            }

    # ----- 数据文件差异视图 -----

    def create_diff(self, base_snapshot_id: str, target_snapshot_id: str) -> DiffView:
        """按物理文件比较两版快照，产出移除/改写/补进三类分开的固化视图。"""
        with self._lock:
            base = self._snapshots.get(base_snapshot_id)
            target = self._snapshots.get(target_snapshot_id)
            if base is None or target is None:
                raise KeyError("差异比较需要两个已存在的快照")
            if base["table"] != target["table"]:
                raise ValueError("仅支持同一湖表的两版快照比较")
            table = base["table"]

            base_by_path = {
                self._objects[oid].path: self._objects[oid]
                for oid in base["objects"]
            }
            target_by_path = {
                self._objects[oid].path: self._objects[oid]
                for oid in target["objects"]
            }

            removed: list[DiffItem] = []
            rewritten: list[DiffItem] = []
            added: list[DiffItem] = []

            for path, old_obj in sorted(base_by_path.items()):
                if path not in target_by_path:
                    removed.append(DiffItem(
                        category="removed",
                        object_id=old_obj.object_id,
                        table=table,
                        path=path,
                        checksum=old_obj.checksum,
                        size_bytes=old_obj.size_bytes,
                        origin_snapshot_id=base_snapshot_id,
                    ))
                else:
                    new_obj = target_by_path[path]
                    if new_obj.object_id != old_obj.object_id or new_obj.checksum != old_obj.checksum:
                        rewritten.append(DiffItem(
                            category="rewritten",
                            object_id=old_obj.object_id,
                            table=table,
                            path=path,
                            checksum=old_obj.checksum,
                            size_bytes=old_obj.size_bytes,
                            origin_snapshot_id=base_snapshot_id,
                            paired_object_id=new_obj.object_id,
                        ))

            for path, new_obj in sorted(target_by_path.items()):
                if path not in base_by_path:
                    added.append(DiffItem(
                        category="added",
                        object_id=new_obj.object_id,
                        table=table,
                        path=path,
                        checksum=new_obj.checksum,
                        size_bytes=new_obj.size_bytes,
                        origin_snapshot_id=target_snapshot_id,
                    ))

            view = DiffView(
                diff_id=self._next_id("diff"),
                table=table,
                base_snapshot_id=base_snapshot_id,
                target_snapshot_id=target_snapshot_id,
                created_at=self._now(),
                removed=removed,
                rewritten=rewritten,
                added=added,
            )
            self._diffs[view.diff_id] = view
            return view

    def list_diffs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [view.to_dict() for view in self._diffs.values()]

    def get_diff(self, diff_id: str) -> dict[str, Any] | None:
        with self._lock:
            view = self._diffs.get(diff_id)
            return view.to_dict() if view else None

    # ----- 回收复核 -----

    def create_review(self, diff_id: str, operator: str = "值班员") -> Review:
        """从差异视图接收复核范围：移除项 + 改写前的旧物理对象。"""
        with self._lock:
            view = self._diffs.get(diff_id)
            if view is None:
                raise KeyError(f"差异视图 {diff_id} 不存在")
            items: list[ReviewItem] = []
            for item in view.removed:
                items.append(ReviewItem(
                    object_id=item.object_id, table=item.table, path=item.path,
                    checksum=item.checksum, origin_snapshot_id=item.origin_snapshot_id,
                    source_category="removed",
                ))
            for item in view.rewritten:
                items.append(ReviewItem(
                    object_id=item.object_id, table=item.table, path=item.path,
                    checksum=item.checksum, origin_snapshot_id=item.origin_snapshot_id,
                    source_category="rewritten-old",
                ))
            review = Review(
                review_id=self._next_id("review"),
                diff_id=diff_id,
                table=view.table,
                base_snapshot_id=view.base_snapshot_id,
                created_at=self._now(),
                operator=operator,
                items=items,
            )
            self._reviews[review.review_id] = review
            return review

    def list_reviews(self) -> list[dict[str, Any]]:
        with self._lock:
            return [review.to_dict() for review in self._reviews.values()]

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        with self._lock:
            review = self._reviews.get(review_id)
            if review is None:
                return None
            payload = review.to_dict()
            # 实时附带每个候选对象当前的引用与释放状态，便于回查下一次复核范围。
            # 基准快照的引用属于本差异的旧范围，不计入活动引用。
            payload["live_reference"] = [
                {
                    "object_id": item.object_id,
                    "path": item.path,
                    "referring_snapshots": self._snapshots_referring(
                        item.object_id, exclude=review.base_snapshot_id,
                    ),
                    "released": self._objects[item.object_id].released,
                }
                for item in review.items
            ]
            return payload

    # ----- 历史清理执行与回执 -----

    def execute_vacuum(
        self,
        review_id: str,
        operator: str,
        *,
        fail_after: int | None = None,
    ) -> dict[str, Any]:
        """按复核范围执行清理，逐件写文件处置清单并返回执行回执。

        - 同一复核单重复/并发提交只确认一次释放（幂等）。
        - fail_after 模拟保存回执中断：已写入的处置记录保留，重试从未处置项继续。
        - 执行时刻重新核对快照引用，仍被任何快照引用的对象不计入真实释放。
        """
        with self._lock:
            review = self._reviews.get(review_id)
            if review is None:
                raise KeyError(f"回收复核单 {review_id} 不存在")

            # 幂等：已有完成回执直接回放，绝不重复释放
            completed = [
                receipt for receipt in self._receipts.values()
                if receipt["review_id"] == review_id and receipt["status"] == "已完成"
            ]
            if completed:
                return dict(completed[0], idempotent_replay=True)

            # 中断回执复用同一身份重试
            interrupted = [
                receipt for receipt in self._receipts.values()
                if receipt["review_id"] == review_id and receipt["status"] == "中断"
            ]
            if interrupted:
                receipt = interrupted[0]
                receipt["attempts"].append({
                    "operator": operator, "at": self._now(), "action": "resume",
                })
            else:
                receipt = {
                    "receipt_id": self._next_id("receipt"),
                    "review_id": review_id,
                    "table": review.table,
                    "operator": operator,
                    "status": "清理中",
                    "started_at": self._now(),
                    "finished_at": None,
                    "attempts": [{
                        "operator": operator, "at": self._now(), "action": "start",
                    }],
                    "items": [],
                    "released_count": 0,
                    "skipped_count": 0,
                }
                self._receipts[receipt["receipt_id"]] = receipt
                review.receipt_ids.append(receipt["receipt_id"])
                review.status = "清理中"

            handled_ids = {row["object_id"] for row in self._ledger
                           if row["receipt_id"] == receipt["receipt_id"]}

            for item in review.items:
                if item.object_id in handled_ids:
                    continue
                obj = self._objects[item.object_id]
                referring = self._snapshots_referring(
                    item.object_id, exclude=review.base_snapshot_id,
                )
                decided_at = self._now()

                if referring or obj.released:
                    # 仍被其他快照引用（或已被先前回执释放）：保住快照可读数据
                    reasons = []
                    if referring:
                        reasons.append(f"仍被快照 {'、'.join(referring)} 引用，跳过物理释放")
                    if obj.released:
                        reasons.append("对象已在此前回执中释放")
                    row = {
                        "receipt_id": receipt["receipt_id"],
                        "review_id": review_id,
                        "table": item.table,
                        "object_id": item.object_id,
                        "path": item.path,
                        "checksum": item.checksum,
                        "origin_snapshot_id": item.origin_snapshot_id,
                        "operator": operator,
                        "decided_at": decided_at,
                        "actually_released": False,
                        "reason": "；".join(reasons),
                        "referring_snapshots": referring,
                    }
                    receipt["skipped_count"] += 1
                else:
                    obj.released = True
                    obj.released_at = decided_at
                    obj.released_by_receipt = receipt["receipt_id"]
                    row = {
                        "receipt_id": receipt["receipt_id"],
                        "review_id": review_id,
                        "table": item.table,
                        "object_id": item.object_id,
                        "path": item.path,
                        "checksum": item.checksum,
                        "origin_snapshot_id": item.origin_snapshot_id,
                        "operator": operator,
                        "decided_at": decided_at,
                        "actually_released": True,
                        "reason": "无活动快照引用，物理文件已释放",
                        "referring_snapshots": [],
                    }
                    receipt["released_count"] += 1

                self._ledger.append(row)
                receipt["items"].append(dict(row))
                handled_ids.add(item.object_id)

                # 模拟回执保存中断：处置清单已逐件落盘，回执保持中断态
                if fail_after is not None and len(handled_ids) >= fail_after:
                    receipt["status"] = "中断"
                    receipt["attempts"].append({
                        "operator": operator, "at": self._now(),
                        "action": "interrupted",
                    })
                    review.status = "中断"
                    raise ReceiptSaveInterrupted(
                        f"回执 {receipt['receipt_id']} 保存中断，已处置 {len(handled_ids)} 件，"
                        "已落盘记录可在重试时接续"
                    )

            receipt["status"] = "已完成"
            receipt["finished_at"] = self._now()
            receipt["attempts"].append({
                "operator": operator, "at": self._now(), "action": "finish",
            })
            review.status = "已完成"
            return dict(receipt, idempotent_replay=False)

    def get_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        with self._lock:
            receipt = self._receipts.get(receipt_id)
            return dict(receipt) if receipt else None

    def list_receipts(self, review_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            receipts = list(self._receipts.values())
            if review_id:
                receipts = [r for r in receipts if r["review_id"] == review_id]
            return [dict(r) for r in receipts]

    # ----- 文件处置清单 -----

    def list_ledger(
        self,
        *,
        review_id: str | None = None,
        object_id: str | None = None,
        actually_released: bool | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._ledger
            if review_id:
                rows = [row for row in rows if row["review_id"] == review_id]
            if object_id:
                rows = [row for row in rows if row["object_id"] == object_id]
            if actually_released is not None:
                rows = [row for row in rows
                        if row["actually_released"] is actually_released]
            return [dict(row) for row in rows]

    def get_object(self, object_id: str) -> dict[str, Any] | None:
        with self._lock:
            obj = self._objects.get(object_id)
            if obj is None:
                return None
            payload = obj.to_dict()
            payload["referring_snapshots"] = self._snapshots_referring(object_id)
            return payload

    def lookup_object(self, table: str, path: str) -> dict[str, Any] | None:
        with self._lock:
            object_id = self._object_index.get((table, path))
            if object_id is None:
                return None
            return self.get_object(object_id)

    def next_review_scope(self, review_id: str) -> dict[str, Any]:
        """回查下一次复核范围：原范围内仍未真正释放的对象及其当前引用。"""
        with self._lock:
            review = self._reviews.get(review_id)
            if review is None:
                raise KeyError(f"回收复核单 {review_id} 不存在")
            pending = []
            for item in review.items:
                obj = self._objects[item.object_id]
                if not obj.released:
                    pending.append({
                        "object_id": item.object_id,
                        "path": item.path,
                        "origin_snapshot_id": item.origin_snapshot_id,
                        "referring_snapshots": self._snapshots_referring(
                            item.object_id, exclude=review.base_snapshot_id,
                        ),
                    })
            return {
                "review_id": review_id,
                "original_scope_count": len(review.items),
                "pending_count": len(pending),
                "pending_items": pending,
            }


# 进程内单例
registry = LakehouseRegistry()
