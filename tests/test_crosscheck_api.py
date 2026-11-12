"""双来源核对 API 集成测试：TestClient + MockTransport 挂载两套离线样例。

/sample/*    -> sample_site   （招标网站 A）
/sample-b/*  -> sample_site_b （招标网站 B）
通过替换处理器模拟分页/详情失败，验证“保留已知对照 + 待核实标注”。
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

SITE_A = config.SAMPLE_DIR
SITE_B = config.SAMPLE_B_DIR
URL_A = "http://testserver/sample/catalog/page_1.html"
URL_B = "http://testserver/sample-b/catalog/page_1.html"


def _static_handler(
    site_root: Path, prefix: str, request: httpx.Request
) -> httpx.Response | None:
    path = request.url.path
    if not path.startswith(prefix):
        return None
    rel = path[len(prefix):]
    f = (site_root / rel).resolve()
    if not str(f).startswith(str(site_root.resolve())) or not f.is_file():
        return httpx.Response(404, text="not found")
    return httpx.Response(
        200,
        content=f.read_bytes(),
        headers={"content-type": "text/html; charset=utf-8"},
    )


def _default_handler(request: httpx.Request) -> httpx.Response:
    for root, prefix in ((SITE_A, "/sample/"), (SITE_B, "/sample-b/")):
        resp = _static_handler(root, prefix, request)
        if resp is not None:
            return resp
    return httpx.Response(404, text="not found")


_HANDLER = {"fn": _default_handler}


def _dispatch(request: httpx.Request) -> httpx.Response:
    return _HANDLER["fn"](request)


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    _HANDLER["fn"] = _default_handler
    transport = httpx.MockTransport(_dispatch)
    real_async_client = httpx.AsyncClient

    class PatchedAsyncClient(real_async_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)
    return TestClient(app)


def _post(client: TestClient) -> dict:
    resp = client.post(
        "/api/crosscheck",
        json={
            "source_a": URL_A,
            "source_b": URL_B,
            "source_a_name": "招标网站A",
            "source_b_name": "招标网站B",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_crosscheck_full_offline_flow(client):
    body = _post(client)
    assert body["status"] == "ok"
    assert body["total_tenders"] == 5

    # 两侧各自的采集汇总
    summary = {s["source"]: s for s in body["sources"]}
    assert summary["A"]["total_announcements"] == 18
    assert summary["A"]["total_pages"] == 3
    assert summary["B"]["total_announcements"] == 19
    assert summary["B"]["total_pages"] == 4
    assert summary["A"]["issues"] == [] and summary["B"]["issues"] == []

    by_code = {t["tender_code"]: t for t in body["tenders"]}

    # ZG-2026-002 完全一致
    t2 = by_code["ZG-2026-002"]
    assert t2["status"] == "consistent"
    assert all(p["status"] == "consistent" for p in t2["pairs"])

    # ZG-2026-001：A015 依据公告冲突（A=A010，B=A005）
    t1 = by_code["ZG-2026-001"]
    assert t1["status"] == "conflict"
    a015 = next(p for p in t1["pairs"] if p["announcement_id"] == "A015")
    assert a015["status"] == "conflict"
    based_conflict = next(c for c in a015["field_conflicts"] if c["field"] == "based_on_id")
    assert based_conflict["source_a"] == "A010"
    assert based_conflict["source_b"] == "A005"
    # 冲突字段带双方来源链接
    assert a015["source_a"]["source_url"].endswith("/sample/detail/A015.html")
    assert a015["source_b"]["source_url"].endswith("/sample-b/detail/A015.html")

    # ZG-2026-003：A013 截止时间冲突
    t3 = by_code["ZG-2026-003"]
    a013 = next(p for p in t3["pairs"] if p["announcement_id"] == "A013")
    deadline_conflict = next(c for c in a013["field_conflicts"] if c["field"] == "deadline")
    assert deadline_conflict["source_a"] == "2026-12-08 09:30"
    assert deadline_conflict["source_b"] == "2026-12-09 09:30"

    # ZG-2026-004：B 站缺 A014（撤回公告）
    t4 = by_code["ZG-2026-004"]
    assert t4["status"] == "one_side_missing"
    a014 = next(p for p in t4["pairs"] if p["announcement_id"] == "A014")
    assert a014["status"] == "one_side_missing"
    assert a014["source_a"] is not None and a014["source_b"] is None
    assert a014["type"] == "撤回公告"
    assert t4["needs_verification"] is False

    # ZG-2026-005：仅 B 站存在
    t5 = by_code["ZG-2026-005"]
    assert t5["status"] == "one_side_missing"
    assert {p["announcement_id"] for p in t5["pairs"]} == {"B001", "B002"}
    assert all(p["source_a"] is None for p in t5["pairs"])

    # 版本链序号连续
    for tender in body["tenders"]:
        assert [p["version_index"] for p in tender["pairs"]] == list(
            range(len(tender["pairs"]))
        )

    # 计数与逐配对状态一致
    counted = {"consistent": 0, "one_side_missing": 0, "conflict": 0, "uncertain": 0}
    for tender in body["tenders"]:
        for pair in tender["pairs"]:
            counted[pair["status"]] += 1
    assert counted == body["counts"]


def test_crosscheck_with_detail_failure_keeps_known_pairs(client):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        # B 站 A013 详情页模拟故障
        if request.url.path == "/sample-b/detail/A013.html":
            raise httpx.ConnectError("simulated outage on site B")
        return _default_handler(request)

    _HANDLER["fn"] = failing_handler
    body = _post(client)
    assert body["status"] == "partial"

    summary_b = next(s for s in body["sources"] if s["source"] == "B")
    assert summary_b["issues"][0]["stage"] == "detail"
    assert summary_b["issues"][0]["url"].endswith("A013.html")

    t3 = next(t for t in body["tenders"] if t["tender_code"] == "ZG-2026-003")
    assert t3["needs_verification"] is True
    a013 = next(p for p in t3["pairs"] if p["announcement_id"] == "A013")
    assert a013["needs_verification"] is True
    # A 侧数据保留；因 B 侧缺失，状态为单方缺失而非凭空判定冲突
    assert a013["status"] == "one_side_missing"
    assert a013["source_a"] is not None and a013["source_b"] is None

    # 同标段其余配对的已知对照不受影响
    a012 = next(p for p in t3["pairs"] if p["announcement_id"] == "A012")
    assert a012["status"] == "consistent"
    # 无关标段不被标记
    t2 = next(t for t in body["tenders"] if t["tender_code"] == "ZG-2026-002")
    assert t2["needs_verification"] is False


def test_crosscheck_with_catalog_failure_truncates_and_marks_all_tenders(client):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sample/catalog/page_2.html":
            raise httpx.ConnectError("simulated catalog outage on site A")
        return _default_handler(request)

    _HANDLER["fn"] = failing_handler
    body = _post(client)
    assert body["status"] == "partial"

    summary_a = next(s for s in body["sources"] if s["source"] == "A")
    assert summary_a["truncated"] is True
    assert summary_a["total_pages"] == 1
    issue = summary_a["issues"][0]
    assert issue["stage"] == "catalog" and "page_2" in issue["url"]

    # A 站只抓到第一页（6 条），已知对照保留；分页截断意味着未读页面可能含有
    # 任意标段的公告（“单方缺失”结论也可能被推翻），故全部标段标待核实
    assert summary_a["total_announcements"] == 6
    for tender in body["tenders"]:
        assert tender["needs_verification"] is True
        assert any("分页" in n for n in tender["notes"])

    # 仍能给出具体的已知对照（A001 两站一致）
    t1 = next(t for t in body["tenders"] if t["tender_code"] == "ZG-2026-001")
    a001 = next(p for p in t1["pairs"] if p["announcement_id"] == "A001")
    assert a001["status"] == "consistent"
    assert a001["source_a"] is not None and a001["source_b"] is not None


def test_crosscheck_both_sites_down_returns_failed_with_200(client):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith(("/sample/", "/sample-b/")):
            raise httpx.ConnectError("both sites down")
        return _default_handler(request)

    _HANDLER["fn"] = failing_handler
    resp = client.post(
        "/api/crosscheck",
        json={"source_a": URL_A, "source_b": URL_B},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["total_tenders"] == 0
    assert {s["source"] for s in body["sources"]} == {"A", "B"}
    assert all(s["truncated"] for s in body["sources"])
    assert body["counts"] == {
        "consistent": 0,
        "one_side_missing": 0,
        "conflict": 0,
        "uncertain": 0,
    }


def test_sample_b_site_is_served_offline(client):
    page = client.get("/sample-b/catalog/page_1.html")
    assert page.status_code == 200
    assert "招标网站 B" in page.text
    detail = client.get("/sample-b/detail/A013.html")
    assert detail.status_code == 200
    assert 'id="announcement-meta"' in detail.text
    assert "2026-12-09 09:30" in detail.text
