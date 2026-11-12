"""双来源核对逻辑单元测试（直接构造采集结果，不依赖网络）。"""
from __future__ import annotations

from app.crawler import FailedEntry, PartialCrawlResult
from app.crosscheck import cross_check
from app.models import Announcement, AnnouncementType as T

URL_A = "http://a/detail/{}.html"
URL_B = "http://b/detail/{}.html"


def ann(aid, code, atype, published, deadline=None, based=None, withdraws=None,
        source="A", project="测试项目"):
    template = URL_A if source == "A" else URL_B
    return Announcement(
        announcement_id=aid,
        tender_code=code,
        project_name=project,
        type=atype,
        title=f"{aid}-title",
        published_at=published,
        deadline=deadline,
        based_on_id=based,
        withdraws_id=withdraws,
        source_url=template.format(aid),
    )


def partial(announcements, failures=None, truncated=False, pages=1):
    return PartialCrawlResult(
        announcements=announcements,
        pages=pages,
        truncated=truncated,
        failures=failures or [],
    )


def run(a_result, b_result, **kwargs):
    return cross_check(
        source_a=a_result,
        source_b=b_result,
        catalog_a="http://a/catalog/page_1.html",
        catalog_b="http://b/catalog/page_1.html",
        checked_at="2026-11-12T00:00:00+00:00",
        **kwargs,
    ).model_dump()


def test_identical_sources_are_consistent():
    a_items = [
        ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30"),
        ann("R1", "C1", T.RESCHEDULE, "2026-11-05 09:00", "2026-11-25 09:30", based="O1"),
        ann("W1", "C1", T.WITHDRAWAL, "2026-11-09 09:00", withdraws="R1"),
    ]
    b_items = [
        ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30", source="B"),
        ann("R1", "C1", T.RESCHEDULE, "2026-11-05 09:00", "2026-11-25 09:30",
            based="O1", source="B"),
        ann("W1", "C1", T.WITHDRAWAL, "2026-11-09 09:00",
            withdraws="R1", source="B"),
    ]
    body = run(partial(a_items), partial(b_items))
    assert body["status"] == "ok"
    assert body["counts"]["consistent"] == 3
    assert body["counts"]["conflict"] == 0
    tender = body["tenders"][0]
    assert tender["status"] == "consistent"
    assert tender["needs_verification"] is False
    # 版本链按发布时间编号
    assert [p["announcement_id"] for p in tender["pairs"]] == ["O1", "R1", "W1"]
    assert [p["version_index"] for p in tender["pairs"]] == [0, 1, 2]
    # 双方来源链接都保留
    pair = tender["pairs"][1]
    assert pair["source_a"]["source_url"].startswith("http://a/")
    assert pair["source_b"]["source_url"].startswith("http://b/")


def test_each_conflicted_field_is_reported_with_both_values():
    a_items = [
        ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30"),
        ann("R1", "C1", T.RESCHEDULE, "2026-11-05 09:00", "2026-11-25 09:30", based="O1"),
    ]
    b_items = [
        ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30", source="B"),
        # 类型、截止时间、依据三个字段同时冲突
        ann("R1", "C1", T.ORIGINAL, "2026-11-05 09:00", "2026-11-26 10:00",
            based="O9", source="B"),
    ]
    body = run(partial(a_items), partial(b_items))
    tender = body["tenders"][0]
    assert tender["status"] == "conflict"
    r1 = next(p for p in tender["pairs"] if p["announcement_id"] == "R1")
    fields = {c["field"]: c for c in r1["field_conflicts"]}
    assert set(fields) == {"type", "deadline", "based_on_id"}
    assert fields["deadline"]["source_a"] == "2026-11-25 09:30"
    assert fields["deadline"]["source_b"] == "2026-11-26 10:00"
    assert fields["type"]["source_a"] == "改期公告"
    assert fields["type"]["source_b"] == "招标公告"
    assert fields["based_on_id"]["source_a"] == "O1"
    assert fields["based_on_id"]["source_b"] == "O9"
    assert body["counts"]["conflict"] == 1


def test_withdraw_relation_conflict_detected():
    a_items = [ann("W1", "C1", T.WITHDRAWAL, "2026-11-09 09:00", withdraws="R1")]
    b_items = [ann("W1", "C1", T.WITHDRAWAL, "2026-11-09 09:00",
                   withdraws="R2", source="B")]
    body = run(partial(a_items), partial(b_items))
    pair = body["tenders"][0]["pairs"][0]
    assert pair["status"] == "conflict"
    conflict = pair["field_conflicts"][0]
    assert conflict["field"] == "withdraws_id"
    assert conflict["source_a"] == "R1"
    assert conflict["source_b"] == "R2"


def test_one_side_missing_in_both_directions():
    a_items = [
        ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30"),
        ann("R1", "C1", T.RESCHEDULE, "2026-11-05 09:00", "2026-11-25 09:30", based="O1"),
    ]
    b_items = [
        ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30", source="B"),
        ann("R2", "C1", T.RESCHEDULE, "2026-11-06 09:00", "2026-11-27 09:30",
            based="O1", source="B"),
    ]
    body = run(partial(a_items), partial(b_items))
    tender = body["tenders"][0]
    assert tender["status"] == "one_side_missing"
    by_id = {p["announcement_id"]: p for p in tender["pairs"]}
    r1 = by_id["R1"]
    assert r1["status"] == "one_side_missing"
    assert r1["source_b"] is None and r1["source_a"] is not None
    assert any("来源B缺少" in n for n in r1["notes"])
    r2 = by_id["R2"]
    assert r2["source_a"] is None and r2["source_b"] is not None
    assert body["counts"]["one_side_missing"] == 2


def test_detail_failure_marks_known_pair_for_verification_but_keeps_comparison():
    # R1 在 A 站详情抓取失败（但 B 站成功）；O1 两侧一致
    a_items = [ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30")]
    b_items = [
        ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30", source="B"),
        ann("R1", "C1", T.RESCHEDULE, "2026-11-05 09:00", "2026-11-25 09:30",
            based="O1", source="B"),
    ]
    a_result = partial(
        a_items,
        failures=[FailedEntry("detail", URL_A.format("R1"), "模拟详情页 500", "C1")],
    )
    body = run(a_result, partial(b_items))
    assert body["status"] == "partial"
    tender = body["tenders"][0]
    assert tender["needs_verification"] is True
    by_id = {p["announcement_id"]: p for p in tender["pairs"]}
    # R1 仅 B 侧有数据：单方缺失且待核实（不是静默丢弃）
    assert by_id["R1"]["status"] == "one_side_missing"
    assert by_id["R1"]["needs_verification"] is True
    # O1 的已知对照不受影响，结论保留
    assert by_id["O1"]["status"] == "consistent"
    assert body["sources"][0]["issues"][0]["stage"] == "detail"


def test_detail_failure_both_sides_is_uncertain():
    a_result = partial(
        [],
        failures=[FailedEntry("detail", URL_A.format("R1"), "A 站解析失败", "C1")],
    )
    b_result = partial(
        [],
        failures=[FailedEntry("detail", URL_B.format("R1"), "B 站连接超时", "C1")],
    )
    body = run(a_result, b_result)
    pair = body["tenders"][0]["pairs"][0]
    assert pair["status"] == "uncertain"
    assert pair["needs_verification"] is True
    assert pair["source_a"] is None and pair["source_b"] is None
    assert body["counts"]["uncertain"] == 1
    assert body["tenders"][0]["status"] == "uncertain"


def test_catalog_truncation_marks_tender_for_verification():
    items = [ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30")]
    a_result = partial(
        items,
        failures=[FailedEntry("catalog", "http://a/catalog/page_2.html", "模拟分页中断")],
        truncated=True,
        pages=1,
    )
    b_result = partial(
        [ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30", source="B")]
    )
    body = run(a_result, b_result)
    assert body["status"] == "partial"
    tender = body["tenders"][0]
    assert tender["needs_verification"] is True
    # 已知对照仍保留并正常判定
    assert tender["pairs"][0]["status"] == "consistent"
    assert any("分页" in n and "待核实" in n for n in tender["notes"])
    assert body["sources"][0]["truncated"] is True
    assert body["sources"][1]["truncated"] is False


def test_unattributable_detail_failure_goes_to_unresolved():
    a_result = partial(
        [],
        failures=[FailedEntry("detail", "http://a/detail/unknown.html", "无招标编号上下文")],
    )
    body = run(a_result, partial([]))
    assert body["status"] == "failed"
    assert body["unresolved_failures"][0]["url"].endswith("unknown.html")
    assert body["total_tenders"] == 0


def test_tender_status_priority_and_overall_counts():
    # C1 含冲突，C2 仅一致，C3 单方缺失；整体统计与标段状态分别正确
    a_items = [
        ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30"),
        ann("R1", "C1", T.RESCHEDULE, "2026-11-05 09:00", "2026-11-25 09:30", based="O1"),
        ann("O2", "C2", T.ORIGINAL, "2026-11-01 09:00", "2026-12-01 09:30"),
        ann("O3", "C3", T.ORIGINAL, "2026-11-02 09:00", "2026-12-02 09:30"),
    ]
    b_items = [
        ann("O1", "C1", T.ORIGINAL, "2026-11-01 09:00", "2026-11-20 09:30", source="B"),
        ann("R1", "C1", T.RESCHEDULE, "2026-11-05 09:00", "2026-11-26 09:30",
            based="O1", source="B"),
        ann("O2", "C2", T.ORIGINAL, "2026-11-01 09:00", "2026-12-01 09:30", source="B"),
    ]
    body = run(partial(a_items), partial(b_items))
    by_code = {t["tender_code"]: t for t in body["tenders"]}
    assert by_code["C1"]["status"] == "conflict"
    assert by_code["C2"]["status"] == "consistent"
    assert by_code["C3"]["status"] == "one_side_missing"
    assert body["counts"] == {
        "consistent": 2,
        "one_side_missing": 1,
        "conflict": 1,
        "uncertain": 0,
    }
