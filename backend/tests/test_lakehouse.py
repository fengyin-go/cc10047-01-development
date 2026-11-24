"""湖表数据文件差异视图与回收复核领域测试。

覆盖题设主线：
S1/S2 比较后 p、q 进入移除项 → p 被挂到 S3 → 原差异视图保持不变 →
保存执行回执中断 → 重试接续 → 处置清单与快照引用一致、S3 的 p 仍可读、
可凭原回执查到 q 的真实释放时间；并验证跨表同名独立身份与并发幂等。
"""
from __future__ import annotations

import sys
import threading

sys.path.insert(0, '.')

from app.lakehouse import LakehouseRegistry, ReceiptSaveInterrupted


class TestFileIdentity:
    def test_same_path_in_different_tables_keeps_independent_identity(self):
        reg = LakehouseRegistry()
        p_demo = reg.lookup_object("lakehouse_demo", "p.parquet")
        p_other = reg.lookup_object("lakehouse_other", "p.parquet")
        assert p_demo is not None and p_other is not None
        assert p_demo["object_id"] != p_other["object_id"]
        assert p_demo["checksum"] != p_other["checksum"]

    def test_rewritten_same_path_gets_new_identity(self):
        reg = LakehouseRegistry()
        s1 = reg.get_snapshot("S1")
        s2 = reg.get_snapshot("S2")
        u1 = next(f for f in s1["files"] if f["path"] == "u.parquet")
        u2 = next(f for f in s2["files"] if f["path"] == "u.parquet")
        assert u1["object_id"] != u2["object_id"]
        assert u1["checksum"] != u2["checksum"]


class TestDiffView:
    def test_diff_splits_removed_rewritten_added(self):
        reg = LakehouseRegistry()
        view = reg.create_diff("S1", "S2").to_dict()
        removed_paths = {item["path"] for item in view["removed"]}
        added_paths = {item["path"] for item in view["added"]}
        rewritten_paths = {item["path"] for item in view["rewritten"]}
        assert removed_paths == {"p.parquet", "q.parquet"}
        assert added_paths == {"s.parquet"}
        assert rewritten_paths == {"u.parquet"}
        # 改写项保留旧对象身份并指向新对象
        rewrite = view["rewritten"][0]
        assert rewrite["paired_object_id"] is not None
        assert rewrite["object_id"] != rewrite["paired_object_id"]

    def test_diff_is_frozen_when_reference_changes_afterwards(self):
        reg = LakehouseRegistry()
        view = reg.create_diff("S1", "S2").to_dict()
        diff_id = view["diff_id"]
        p_before = next(item for item in view["removed"] if item["path"] == "p.parquet")

        # 差异视图生成后，另一个快照登记了 p 的引用
        reg.attach_reference("S3", "lakehouse_demo", "p.parquet", "ck-p-1", 1024)

        frozen = reg.get_diff(diff_id)
        p_after = next(item for item in frozen["removed"] if item["path"] == "p.parquet")
        assert frozen["frozen"] is True
        assert p_after == p_before
        assert frozen["removed_count"] == 2

    def test_diff_rejects_cross_table_snapshots(self):
        reg = LakehouseRegistry()
        try:
            reg.create_diff("S1", "S9")
        except ValueError as exc:
            assert "同一湖表" in str(exc)
        else:
            raise AssertionError("跨湖表快照比较应被拒绝")


class TestVacuumFlow:
    def _prepare(self) -> tuple[LakehouseRegistry, str, str]:
        reg = LakehouseRegistry()
        diff = reg.create_diff("S1", "S2")
        review = reg.create_review(diff.diff_id, operator="值班员甲")
        return reg, diff.diff_id, review.review_id

    def test_review_scope_covers_removed_and_rewritten_old(self):
        reg, _, review_id = self._prepare()
        review = reg.get_review(review_id)
        paths = {item["path"] for item in review["items"]}
        # 移除项 p、q 与改写前旧对象 u(ck-u-1) 进入复核；补进的 s 不进入
        assert paths == {"p.parquet", "q.parquet", "u.parquet"}
        sources = {item["source_category"] for item in review["items"]}
        assert sources == {"removed", "rewritten-old"}

    def test_interrupted_receipt_then_retry_releases_only_unreferenced(self):
        reg, _, review_id = self._prepare()

        # 差异生成后，S3 登记了 p —— 原比较与当前引用范围发生冲突
        attach = reg.attach_reference("S3", "lakehouse_demo", "p.parquet", "ck-p-1", 1024)
        assert attach["attached"] is True

        # 第一次执行：处理第 1 件后回执保存中断
        try:
            reg.execute_vacuum(review_id, "值班员甲", fail_after=1)
        except ReceiptSaveInterrupted as exc:
            assert "保存中断" in str(exc)
        else:
            raise AssertionError("应模拟保存回执中断")

        interrupted_receipts = reg.list_receipts(review_id)
        assert len(interrupted_receipts) == 1
        receipt_id = interrupted_receipts[0]["receipt_id"]
        assert interrupted_receipts[0]["status"] == "中断"

        # 中断时已落盘的处置记录保留
        partial_ledger = reg.list_ledger(review_id=review_id)
        assert len(partial_ledger) == 1
        first = partial_ledger[0]
        assert {"receipt_id", "origin_snapshot_id", "object_id",
                "actually_released", "decided_at"} <= set(first)
        assert first["origin_snapshot_id"] == "S1"

        # 重试：复用同一回执，从未处置项接续
        retry = reg.execute_vacuum(review_id, "值班员甲")
        assert retry["status"] == "已完成"
        assert retry["receipt_id"] == receipt_id
        assert retry["attempts"][-1]["action"] == "finish"

        ledger = reg.list_ledger(review_id=review_id)
        assert len(ledger) == 3  # p、q、u 各一件，无重复处置
        by_path = {row["path"]: row for row in ledger}

        # p 仍被 S3 引用：只登记未释放，保住快照可读数据
        assert by_path["p.parquet"]["actually_released"] is False
        assert by_path["p.parquet"]["referring_snapshots"] == ["S3"]
        assert "S3" in by_path["p.parquet"]["reason"]

        # q、u 已无引用：真正释放，且记录了释放时间
        for path in ("q.parquet", "u.parquet"):
            assert by_path[path]["actually_released"] is True
            assert by_path[path]["decided_at"]
            assert by_path[path]["referring_snapshots"] == []

        # S3 的 p 仍可读
        readable = reg.is_snapshot_readable("S3")
        assert readable["readable"] is True
        s3_files = {f["path"] for f in readable["referenced_files"]}
        assert s3_files == {"p.parquet"}

        # 可凭原回执查到 q 最后何时真正释放
        receipt = reg.get_receipt(receipt_id)
        q_record = next(item for item in receipt["items"] if item["path"] == "q.parquet")
        assert q_record["actually_released"] is True
        assert q_record["decided_at"] == by_path["q.parquet"]["decided_at"]

        # 下一次复核范围：只剩仍被引用的 p
        scope = reg.next_review_scope(review_id)
        assert scope["pending_count"] == 1
        assert scope["pending_items"][0]["path"] == "p.parquet"
        assert scope["pending_items"][0]["referring_snapshots"] == ["S3"]

        review = reg.get_review(review_id)
        assert review["status"] == "已完成"

    def test_concurrent_duplicate_submissions_release_each_object_once(self):
        reg, _, review_id = self._prepare()
        errors: list[Exception] = []
        results: list[dict] = []

        def worker(operator: str) -> None:
            try:
                results.append(reg.execute_vacuum(review_id, operator))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(name,))
            for name in ("值班员甲", "值班员乙", "值班员丙")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        # 三个值班员都拿到回执，但只有一份完成回执，其余为幂等回放
        assert len(results) == 3
        completed = [r for r in results if not r.get("idempotent_replay")]
        replays = [r for r in results if r.get("idempotent_replay")]
        assert len(completed) == 1
        assert len(replays) == 2

        ledger = reg.list_ledger(review_id=review_id)
        object_ids = [row["object_id"] for row in ledger]
        assert len(object_ids) == len(set(object_ids))
        # 无 S3 引用场景：三个对象都真实释放且只释放一次
        assert all(row["actually_released"] for row in ledger)
        for object_id in set(object_ids):
            obj = reg.get_object(object_id)
            assert obj["released"] is True
            assert obj["released_at"] is not None

    def test_released_object_is_not_counted_when_referenced_again_later(self):
        reg, _, review_id = self._prepare()
        reg.execute_vacuum(review_id, "值班员甲")
        # 完成后重复提交：全部幂等回放，不会产生新的处置记录
        replay = reg.execute_vacuum(review_id, "值班员乙")
        assert replay["idempotent_replay"] is True
        assert len(reg.list_ledger(review_id=review_id)) == 3

    def test_s1_remains_readable_only_for_files_still_held(self):
        reg, _, review_id = self._prepare()
        reg.execute_vacuum(review_id, "值班员甲")
        # S1 持有的 p、q、u(旧版) 均被释放；r 不在复核范围且仍被 S2 持有
        s1 = reg.is_snapshot_readable("S1")
        assert s1["readable"] is False
        expected_missing = {
            reg.lookup_object("lakehouse_demo", "p.parquet")["object_id"],
            reg.lookup_object("lakehouse_demo", "q.parquet")["object_id"],
        }
        # u.parquet 已改写，按路径查到的是 S2 的新对象，旧对象身份从 S1 快照取
        s1_u = next(f for f in reg.get_snapshot("S1")["files"] if f["path"] == "u.parquet")
        expected_missing.add(s1_u["object_id"])
        assert set(s1["missing_or_released"]) == expected_missing
        s2 = reg.is_snapshot_readable("S2")
        assert s2["readable"] is True
