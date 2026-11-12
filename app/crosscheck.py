"""双来源公告一致性核对。

输入两个来源各自的（容错）采集结果，按 (招标编号, 公告编号) 配对，
按发布时间形成版本链，逐对比较公告类型、投标截止时间、依据公告与撤回关系，
区分四种配对状态：

- consistent       双方均存在且比对字段一致
- one_side_missing 仅一方存在该公告
- conflict         双方均存在但比对字段存在差异（列出冲突字段及双方取值/来源）
- uncertain        采集失败导致无法判定（受影响标段同时标 needs_verification）

任一分页或详情失败都不丢弃已知对照：已抓到的配对正常核对，受影响部分标为待核实。
"""
from __future__ import annotations

from .crawler import FailedEntry, PartialCrawlResult
from .models import (
    Announcement,
    AnnouncementPair,
    AnnouncementType,
    CrawlIssue,
    CrossCheckResponse,
    FieldConflict,
    SourceAnnouncementRef,
    SourceCrawlSummary,
    TenderCrossCheck,
)

# 参与核对的字段：(字段标识, 中文名)
FIELD_DEFS: list[tuple[str, str]] = [
    ("type", "公告类型"),
    ("deadline", "投标截止时间"),
    ("based_on_id", "依据公告"),
    ("withdraws_id", "撤回关系"),
]

STATUS_CONSISTENT = "consistent"
STATUS_ONE_SIDE_MISSING = "one_side_missing"
STATUS_CONFLICT = "conflict"
STATUS_UNCERTAIN = "uncertain"

# 标段汇总状态的优先级：无法判定 > 冲突 > 单方缺失 > 一致
_STATUS_RANK = {
    STATUS_CONSISTENT: 0,
    STATUS_ONE_SIDE_MISSING: 1,
    STATUS_CONFLICT: 2,
    STATUS_UNCERTAIN: 3,
}


def _norm(value: str | None) -> str:
    """归一化：None 与空字符串等价，去除首尾空白。"""
    return (value or "").strip()


def _id_from_url(url: str) -> str | None:
    """从详情 URL 末段猜测公告编号（样例站点以 <编号>.html 命名）。"""
    name = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".html"):
        name = name[: -len(".html")]
    return name or None


def _group(announcements: list[Announcement]) -> dict[str, dict[str, Announcement]]:
    """按 招标编号 -> 公告编号 建索引。"""
    grouped: dict[str, dict[str, Announcement]] = {}
    for ann in announcements:
        grouped.setdefault(ann.tender_code, {})[ann.announcement_id] = ann
    return grouped


def _ref(ann: Announcement) -> SourceAnnouncementRef:
    return SourceAnnouncementRef(
        announcement_id=ann.announcement_id,
        type=ann.type,
        published_at=ann.published_at,
        deadline=ann.deadline,
        based_on_id=ann.based_on_id,
        withdraws_id=ann.withdraws_id,
        source_url=ann.source_url,
    )


def _field_value(ann: Announcement, field: str) -> str:
    if field == "type":
        return ann.type.value
    return _norm(getattr(ann, field))


def _compare_pair(a: Announcement, b: Announcement) -> list[FieldConflict]:
    conflicts: list[FieldConflict] = []
    for field, label in FIELD_DEFS:
        va, vb = _field_value(a, field), _field_value(b, field)
        if va != vb:
            conflicts.append(
                FieldConflict(
                    field=field,
                    label=label,
                    source_a=va or None,
                    source_b=vb or None,
                )
            )
    return conflicts


def _source_summary(
    source_key: str,
    name: str,
    catalog_url: str,
    result: PartialCrawlResult,
) -> SourceCrawlSummary:
    return SourceCrawlSummary(
        source=source_key,
        name=name,
        catalog_url=catalog_url,
        total_pages=result.pages,
        total_announcements=len(result.announcements),
        truncated=result.truncated,
        issues=[
            CrawlIssue(
                source=source_key,
                stage=f.stage,
                url=f.url,
                error=f.error,
            )
            for f in result.failures
        ],
    )


def cross_check(
    source_a: PartialCrawlResult,
    source_b: PartialCrawlResult,
    catalog_a: str,
    catalog_b: str,
    name_a: str = "来源A",
    name_b: str = "来源B",
    checked_at: str = "",
) -> CrossCheckResponse:
    """执行双来源核对并组装完整响应。"""
    group_a = _group(source_a.announcements)
    group_b = _group(source_b.announcements)

    # 失败明细按 (来源, 招标编号) 归集，用于标段/配对级“待核实”标注
    detail_failures: dict[tuple[str, str], list[FailedEntry]] = {}
    unresolved: list[CrawlIssue] = []
    for key, result in (("A", source_a), ("B", source_b)):
        for f in result.failures:
            if f.stage == "detail" and f.tender_code:
                detail_failures.setdefault((key, f.tender_code), []).append(f)
            elif f.stage == "detail":
                unresolved.append(
                    CrawlIssue(source=key, stage=f.stage, url=f.url, error=f.error)
                )

    # 详情失败但能从 URL 猜出公告编号时，生成 uncertain 占位配对
    # (招标编号, 公告编号) -> 占位信息 [(来源, 错误描述)]
    placeholders: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for (key, code), fails in detail_failures.items():
        for f in fails:
            guess_id = _id_from_url(f.url)
            if guess_id:
                placeholders.setdefault((code, guess_id), []).append((key, f.error))

    all_codes = sorted(set(group_a) | set(group_b) | {code for code, _ in placeholders})

    tenders: list[TenderCrossCheck] = []
    counts = {
        STATUS_CONSISTENT: 0,
        STATUS_ONE_SIDE_MISSING: 0,
        STATUS_CONFLICT: 0,
        STATUS_UNCERTAIN: 0,
    }
    notes: list[str] = []

    for code in all_codes:
        map_a = group_a.get(code, {})
        map_b = group_b.get(code, {})
        pair_ids = set(map_a) | set(map_b)
        for ph_code, ph_id in placeholders:
            if ph_code == code:
                pair_ids.add(ph_id)

        # 版本链：按发布时间排序（双方发布时间不一致时以 A 侧为准，差异记入备注）
        def _sort_key(pid: str) -> tuple[str, str]:
            ann = map_a.get(pid) or map_b.get(pid)
            return (ann.published_at if ann else "", pid)

        ordered_ids = sorted(pair_ids, key=_sort_key)
        pairs: list[AnnouncementPair] = []
        tender_notes: list[str] = []
        needs_verification = False

        for index, pid in enumerate(ordered_ids):
            a = map_a.get(pid)
            b = map_b.get(pid)
            placeholder = placeholders.get((code, pid), [])

            if a is not None and b is not None:
                field_conflicts = _compare_pair(a, b)
                status = STATUS_CONFLICT if field_conflicts else STATUS_CONSISTENT
                pair_notes: list[str] = []
                if _norm(a.published_at) != _norm(b.published_at):
                    pair_notes.append(
                        f"双方发布时间不一致：{name_a}={a.published_at}，"
                        f"{name_b}={b.published_at}；版本链序号按 {name_a} 排序"
                    )
                pair = AnnouncementPair(
                    announcement_id=pid,
                    tender_code=code,
                    type=a.type,
                    status=status,
                    needs_verification=False,
                    version_index=index,
                    source_a=_ref(a),
                    source_b=_ref(b),
                    field_conflicts=field_conflicts,
                    notes=pair_notes,
                )
            elif a is not None or b is not None:
                present = a or b
                assert present is not None
                missing_side = name_b if a is not None else name_a
                pair = AnnouncementPair(
                    announcement_id=pid,
                    tender_code=code,
                    type=present.type,
                    status=STATUS_ONE_SIDE_MISSING,
                    needs_verification=False,
                    version_index=index,
                    source_a=_ref(a) if a is not None else None,
                    source_b=_ref(b) if b is not None else None,
                    notes=[f"{missing_side}缺少该公告，仅单方数据，无法交叉核对"],
                )
            else:
                # 双方详情均未解析成功（或失败 URL 推导出的编号）：无法判定
                pair = AnnouncementPair(
                    announcement_id=pid,
                    tender_code=code,
                    type=None,
                    status=STATUS_UNCERTAIN,
                    needs_verification=True,
                    version_index=index,
                    source_a=None,
                    source_b=None,
                    notes=[
                        f"来源{key}详情页抓取/解析失败：{err}" for key, err in placeholder
                    ]
                    or ["双方均无该公告的可用数据，无法判定"],
                )

            # 已成功配对的公告若其所在来源/标段存在详情失败，同样标待核实
            if placeholder and pair.status != STATUS_UNCERTAIN:
                pair.needs_verification = True
                for key, err in placeholder:
                    pair.notes.append(f"来源{key}存在同编号页面失败记录：{err}")
            needs_verification = needs_verification or pair.needs_verification
            counts[pair.status] += 1
            pairs.append(pair)

        # 分页截断：未读页面可能含有任意标段的公告，本标段全部结论均需人工复核
        for key, result, name in (
            ("A", source_a, name_a),
            ("B", source_b, name_b),
        ):
            if result.truncated:
                needs_verification = True
                tender_notes.append(
                    f"{name}目录分页在第 {result.pages} 页后中断"
                    "（分页抓取失败），未读页面可能含本标段公告，结论待核实"
                )

        # 标段级详情失败备注（无法归并到具体配对的部分）
        for key, result, name in (
            ("A", source_a, name_a),
            ("B", source_b, name_b),
        ):
            for f in result.failures:
                if f.stage == "detail" and f.tender_code == code:
                    guess_id = _id_from_url(f.url)
                    if not guess_id or (code, guess_id) not in placeholders:
                        needs_verification = True
                        tender_notes.append(
                            f"{name}详情页抓取失败（{f.url}）：{f.error}，本标段待核实"
                        )

        tender_status = max(
            (p.status for p in pairs),
            key=lambda s: _STATUS_RANK[s],
            default=STATUS_CONSISTENT,
        )
        project_name = next(
            (ann.project_name for ann in map_a.values()),
            None,
        ) or next((ann.project_name for ann in map_b.values()), None)

        tenders.append(
            TenderCrossCheck(
                tender_code=code,
                project_name=project_name,
                status=tender_status,
                needs_verification=needs_verification,
                pairs=pairs,
                notes=tender_notes,
            )
        )

    # 分页失败但无法归属到任何已知标段（例如第一页就失败）
    for key, result, name in (
        ("A", source_a, name_a),
        ("B", source_b, name_b),
    ):
        for f in result.failures:
            if f.stage == "catalog":
                notes.append(f"{name}分页抓取失败（{f.url}）：{f.error}")

    if not source_a.announcements and not source_b.announcements:
        overall = "failed"
    elif source_a.failures or source_b.failures:
        overall = "partial"
    else:
        overall = "ok"

    return CrossCheckResponse(
        status=overall,
        sources=[
            _source_summary("A", name_a, catalog_a, source_a),
            _source_summary("B", name_b, catalog_b, source_b),
        ],
        total_tenders=len(tenders),
        counts=counts,
        tenders=tenders,
        unresolved_failures=unresolved,
        notes=notes,
        checked_at=checked_at,
    )
