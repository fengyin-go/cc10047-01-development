"""湖表差异与回收复核 HTTP 链路测试：走真实 FastAPI 路由验证题设主线。"""
from __future__ import annotations

import sys

sys.path.insert(0, '.')

from fastapi.testclient import TestClient

from app.main import app
from app.lakehouse import registry
from app.routers import lakehouse  # noqa: F401  确保专用路由先于通用路由注册

client = TestClient(app)


class TestLakehouseApiFlow:
    def test_full_interrupt_and_retry_story(self):
        # 1. 选择 S1 与 S2 生成数据文件差异视图
        resp = client.post("/api/lakehouse/diffs", json={
            "base_snapshot_id": "S1", "target_snapshot_id": "S2",
        })
        assert resp.status_code == 200
        diff = resp.json()
        assert diff["frozen"] is True
        removed_paths = [item["path"] for item in diff["removed"]]
        assert "p.parquet" in removed_paths and "q.parquet" in removed_paths
        assert diff["rewritten"][0]["path"] == "u.parquet"
        assert diff["added"][0]["path"] == "s.parquet"
        diff_id = diff["diff_id"]

        # 2. 从差异视图发起回收复核（范围 = 移除项 + 改写前旧对象）
        resp = client.post("/api/lakehouse/reviews", json={
            "diff_id": diff_id, "operator": "值班员甲",
        })
        assert resp.status_code == 200
        review = resp.json()
        review_id = review["review_id"]
        assert review["item_count"] == 3
        assert review["base_snapshot_id"] == "S1"

        # 3. 另一个快照 S3 刚登记了其中一个原文件 p
        p_object = next(item for item in diff["removed"] if item["path"] == "p.parquet")
        resp = client.post(f"/api/lakehouse/snapshots/S3/attach", json={
            "table": "lakehouse_demo",
            "path": "p.parquet",
            "checksum": p_object["checksum"],
            "size_bytes": p_object["size_bytes"],
        })
        assert resp.status_code == 200

        # 原差异视图保持不变，p 仍在移除项里
        frozen = client.get(f"/api/lakehouse/diffs/{diff_id}").json()
        assert len(frozen["removed"]) == 2

        # 4. 由原比较发起清理，保存执行回执的操作中断（处理 1 件后）
        resp = client.post("/api/lakehouse/vacuum", json={
            "review_id": review_id, "operator": "值班员甲", "fail_after": 1,
        })
        assert resp.status_code == 200
        interrupted = resp.json()
        assert interrupted["ok"] is False
        assert interrupted["interrupted"] is True
        receipt_id = interrupted["receipt"]["receipt_id"]
        assert interrupted["receipt"]["status"] == "中断"

        # 5. 重试后回到历史清理详情
        resp = client.post("/api/lakehouse/vacuum", json={
            "review_id": review_id, "operator": "值班员甲",
        })
        assert resp.status_code == 200
        done = resp.json()
        assert done["ok"] is True
        assert done["receipt"]["receipt_id"] == receipt_id
        assert done["receipt"]["status"] == "已完成"

        # 6. 处置清单逐件记录原快照版本、文件身份、是否真的释放
        ledger = client.get(
            "/api/lakehouse/ledger", params={"review_id": review_id},
        ).json()
        assert ledger["total"] == 3
        by_path = {row["path"]: row for row in ledger["items"]}
        assert all(row["origin_snapshot_id"] == "S1" for row in ledger["items"])
        assert by_path["p.parquet"]["actually_released"] is False
        assert by_path["p.parquet"]["referring_snapshots"] == ["S3"]
        assert by_path["q.parquet"]["actually_released"] is True
        assert by_path["u.parquet"]["actually_released"] is True

        # 7. S3 的 p 仍可读
        readable = client.get("/api/lakehouse/snapshots/S3/readable").json()
        assert readable["readable"] is True
        assert readable["referenced_files"][0]["path"] == "p.parquet"
        assert readable["referenced_files"][0]["released"] is False

        # 8. 凭原回执查到 q 最后何时真正释放
        receipt = client.get(f"/api/lakehouse/receipts/{receipt_id}").json()
        q_record = next(item for item in receipt["items"] if item["path"] == "q.parquet")
        assert q_record["actually_released"] is True
        assert q_record["decided_at"] == by_path["q.parquet"]["decided_at"]

        # 9. 下一次要复核的范围：只剩被 S3 引用的 p
        scope = client.get(f"/api/lakehouse/reviews/{review_id}/next-scope").json()
        assert scope["pending_count"] == 1
        assert scope["pending_items"][0]["path"] == "p.parquet"

    def test_cross_table_same_name_is_independent(self):
        resp = client.get(
            "/api/lakehouse/objects/lookup",
            params={"table": "lakehouse_demo", "path": "p.parquet"},
        )
        demo_p = resp.json()
        resp = client.get(
            "/api/lakehouse/objects/lookup",
            params={"table": "lakehouse_other", "path": "p.parquet"},
        )
        other_p = resp.json()
        assert demo_p["object_id"] != other_p["object_id"]

    def test_generic_resource_router_still_works_after_specific_router(self):
        # 专用路由先行注册后，通用模块路由不能被遮蔽
        resp = client.get("/api/snapshot_mgr")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_domain_service_anchors(self):
        from app.services import service_for
        snap_svc = service_for("snapshot_mgr")
        result = snap_svc.domain_compare_snapshots("S1", "S2")
        assert result["ok"] is True
        assert result["removed_count"] == 2

        vacuum_svc = service_for("vacuum_job")
        review = registry.create_review(result["diff_id"], operator="值班员乙")
        outcome = vacuum_svc.domain_run_vacuum_review(review.review_id, "值班员乙")
        assert outcome["ok"] is True
        ledger = vacuum_svc.domain_disposition_ledger(review.review_id)
        assert ledger["total"] == 3
