#!/usr/bin/env python3
"""生成“招标网站 B”离线样例站点，供双来源一致性核对使用。

与 sample_site（网站 A）同构（相同解析契约），但数据刻意覆盖核对场景：

| 招标编号      | 场景                                                        | 预期核对结果        |
|---------------|-------------------------------------------------------------|---------------------|
| ZG-2026-001   | A015 的“依据公告”与 A 站不一致（A 站依据 A010，B 站依据 A005） | conflict（依据字段）|
| ZG-2026-002   | 全部公告字段一致                                            | consistent          |
| ZG-2026-003   | A013 投标截止时间与 A 站不一致                              | conflict（截止时间）|
| ZG-2026-004   | 缺少 A 站的撤回公告 A014                                    | one_side_missing    |
| ZG-2026-005   | 仅 B 站存在（B001 原公告 + B002 改期）                      | one_side_missing    |

目录结构（全部相对链接、无外部资源）：
  sample_site_b/index.html
  sample_site_b/catalog/page_{1..4}.html
  sample_site_b/detail/<公告ID>.html
"""
from __future__ import annotations

import html
import os
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "sample_site_b")
CATALOG_DIR = os.path.join(SITE, "catalog")
DETAIL_DIR = os.path.join(SITE, "detail")

TYPE_ORIGINAL = "招标公告"
TYPE_RESCHEDULE = "改期公告"
TYPE_WITHDRAWAL = "撤回公告"


@dataclass
class Ann:
    aid: str
    code: str
    atype: str
    title: str
    project: str
    published: str
    deadline: str | None = None
    based_on: str | None = None
    withdraws: str | None = None


# 19 条公告：与 A 站共享 17 个公告编号（其中 2 条字段冲突），
# A 站独有的 A014 在本站缺失；本站另有 B001/B002 构成 B 站独有标段。
ANNOUNCEMENTS: list[Ann] = [
    Ann("A001", "ZG-2026-001", TYPE_ORIGINAL, "市政道路改造工程招标公告",
        "市政道路改造工程", "2026-11-01 09:00", "2026-11-20 09:30"),
    Ann("A004", "ZG-2026-004", TYPE_ORIGINAL, "城市路灯节能改造招标公告",
        "城市路灯节能改造项目", "2026-11-01 10:00", "2026-11-21 09:30"),
    Ann("A002", "ZG-2026-002", TYPE_ORIGINAL, "跨河桥梁加固工程招标公告",
        "跨河桥梁加固工程", "2026-11-01 11:00", "2026-11-22 09:30"),
    Ann("A003", "ZG-2026-003", TYPE_ORIGINAL, "排水管网更新工程招标公告",
        "排水管网更新工程", "2026-11-01 12:00", "2026-11-24 09:30"),
    # B 站独有标段
    Ann("B001", "ZG-2026-005", TYPE_ORIGINAL, "园区绿化提升工程招标公告",
        "园区绿化提升工程", "2026-11-02 09:00", "2026-11-23 09:30"),
    Ann("A005", "ZG-2026-001", TYPE_RESCHEDULE, "市政道路改造工程投标截止时间改期公告（第一次）",
        "市政道路改造工程", "2026-11-05 09:00", "2026-11-25 09:30", based_on="A001"),
    Ann("A006", "ZG-2026-002", TYPE_RESCHEDULE, "跨河桥梁加固工程投标截止时间改期公告（第一次）",
        "跨河桥梁加固工程", "2026-11-05 10:00", "2026-11-26 09:30", based_on="A002"),
    Ann("A007", "ZG-2026-003", TYPE_RESCHEDULE, "排水管网更新工程投标截止时间改期公告（第一次）",
        "排水管网更新工程", "2026-11-05 11:00", "2026-12-01 09:30", based_on="A003"),
    Ann("B002", "ZG-2026-005", TYPE_RESCHEDULE, "园区绿化提升工程投标截止时间改期公告（第一次）",
        "园区绿化提升工程", "2026-11-06 10:00", "2026-12-01 09:30", based_on="B001"),
    Ann("A008", "ZG-2026-004", TYPE_RESCHEDULE, "城市路灯节能改造投标截止时间改期公告（第一次）",
        "城市路灯节能改造项目", "2026-11-06 09:00", "2026-11-27 09:30", based_on="A004"),
    Ann("A009", "ZG-2026-004", TYPE_RESCHEDULE, "城市路灯节能改造投标截止时间改期公告（第二次）",
        "城市路灯节能改造项目", "2026-11-08 09:00", "2026-12-03 09:30", based_on="A008"),
    Ann("A010", "ZG-2026-001", TYPE_RESCHEDULE, "市政道路改造工程投标截止时间改期公告（第二次）",
        "市政道路改造工程", "2026-11-10 09:00", "2026-11-28 14:00", based_on="A005"),
    Ann("A011", "ZG-2026-002", TYPE_RESCHEDULE, "跨河桥梁加固工程投标截止时间改期公告（第二次）",
        "跨河桥梁加固工程", "2026-11-10 10:00", "2026-11-30 09:30", based_on="A006"),
    Ann("A012", "ZG-2026-003", TYPE_RESCHEDULE, "排水管网更新工程投标截止时间改期公告（第二次·甲）",
        "排水管网更新工程", "2026-11-12 09:00", "2026-12-05 09:30", based_on="A007"),
    # 冲突点 1：B 站 A013 截止时间与 A 站（2026-12-08 09:30）不同
    Ann("A013", "ZG-2026-003", TYPE_RESCHEDULE, "排水管网更新工程投标截止时间改期公告（第二次·乙）",
        "排水管网更新工程", "2026-11-12 15:00", "2026-12-09 09:30", based_on="A007"),
    # 注：A 站的撤回公告 A014 在本站缺失 -> one_side_missing
    # 冲突点 2：B 站 A015 依据 A005（A 站依据 A010），平台转载链路口径不一致
    Ann("A015", "ZG-2026-001", TYPE_RESCHEDULE, "市政道路改造工程投标截止时间改期公告（第三次）",
        "市政道路改造工程", "2026-11-15 09:00", "2026-12-02 09:30", based_on="A005"),
    Ann("A016", "ZG-2026-002", TYPE_WITHDRAWAL, "跨河桥梁加固工程改期公告撤回公告",
        "跨河桥梁加固工程", "2026-11-16 09:00", withdraws="A011"),
    Ann("A017", "ZG-2026-002", TYPE_RESCHEDULE, "跨河桥梁加固工程投标截止时间改期公告（第三次）",
        "跨河桥梁加固工程", "2026-11-17 09:00", "2026-12-06 09:30", based_on="A011"),
    Ann("A018", "ZG-2026-001", TYPE_RESCHEDULE, "市政道路改造工程投标截止时间改期公告（第四次）",
        "市政道路改造工程", "2026-11-18 09:00", "2026-12-04 09:30", based_on="A015"),
]

PAGE_SIZE = 6

CSS = """
body { font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; margin: 24px; color: #222; }
h1 { font-size: 20px; } table { border-collapse: collapse; width: 720px; margin-top: 12px; }
th, td { border: 1px solid #999; padding: 6px 10px; text-align: left; font-size: 14px; }
th { background: #eef5ee; width: 130px; }
ul.catalog li { margin: 6px 0; } .pager a { margin-right: 12px; }
.meta-id { color: #666; font-size: 13px; }
.site-badge { display: inline-block; background: #2e7d32; color: #fff; padding: 2px 10px;
              border-radius: 4px; font-size: 13px; }
"""


def detail_html(a: Ann) -> str:
    rows = [
        ("公告编号", a.aid),
        ("招标编号", a.code),
        ("公告类型", a.atype),
        ("项目名称", a.project),
        ("发布时间", a.published),
    ]
    if a.deadline is not None:
        rows.append(("投标截止时间", a.deadline))
    if a.based_on is not None:
        rows.append(("依据公告", f'<a href="{a.based_on}.html">{a.based_on}</a>'))
    if a.withdraws is not None:
        rows.append(("撤回公告", f'<a href="{a.withdraws}.html">{a.withdraws}</a>'))
    body = "".join(f"<tr><th>{html.escape(k)}</th><td>{v}</td></tr>" for k, v in rows)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(a.title)}｜招标网站 B</title>
<style>{CSS}</style>
</head>
<body>
<span class="site-badge">招标网站 B（离线对照样例）</span>
<h1>{html.escape(a.title)}</h1>
<p class="meta-id">公告编号：{a.aid} ｜ 招标编号：{html.escape(a.code)}</p>
<table id="announcement-meta">
{body}
</table>
<p>本页面为离线样例数据，用于双来源公告一致性核对。</p>
<p><a href="../catalog/page_1.html">返回公告目录</a></p>
</body>
</html>
"""


def catalog_html(page: int, total_pages: int, depth: int = 1) -> str:
    """depth=1 页面位于 catalog/ 下（链接带 ../）；depth=0 位于站点根。"""
    prefix = "../" if depth else ""
    start = (page - 1) * PAGE_SIZE
    items = ANNOUNCEMENTS[start:start + PAGE_SIZE]
    lis = []
    for a in items:
        lis.append(
            '<li><span class="meta-id">'
            f'[{html.escape(a.aid)}] {html.escape(a.published)}</span> '
            f'<a href="{prefix}detail/{a.aid}.html">{html.escape(a.title)}</a>'
            f' （招标编号：{html.escape(a.code)}）</li>'
        )
    pager = []
    for p in range(1, total_pages + 1):
        if p == page:
            pager.append(f"<strong>第 {p} 页</strong>")
        else:
            pager.append(f'<a href="{prefix}catalog/page_{p}.html">第 {p} 页</a>')
    if page < total_pages:
        pager.append(
            f'<a rel="next" href="{prefix}catalog/page_{page + 1}.html">下一页 &raquo;</a>'
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>招标网站 B 公告目录 - 第 {page} 页</title>
<style>{CSS}</style>
</head>
<body>
<span class="site-badge">招标网站 B（离线对照样例）</span>
<h1>招标网站 B 公告目录（第 {page} / {total_pages} 页）</h1>
<ul class="catalog">
{''.join(lis)}
</ul>
<p class="pager">{''.join(pager)}</p>
</body>
</html>
"""


def main() -> None:
    os.makedirs(CATALOG_DIR, exist_ok=True)
    os.makedirs(DETAIL_DIR, exist_ok=True)
    total_pages = (len(ANNOUNCEMENTS) + PAGE_SIZE - 1) // PAGE_SIZE
    for a in ANNOUNCEMENTS:
        with open(os.path.join(DETAIL_DIR, f"{a.aid}.html"), "w", encoding="utf-8") as f:
            f.write(detail_html(a))
    for p in range(1, total_pages + 1):
        with open(os.path.join(CATALOG_DIR, f"page_{p}.html"), "w", encoding="utf-8") as f:
            f.write(catalog_html(p, total_pages, depth=1))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(catalog_html(1, total_pages, depth=0))
    print(f"generated {len(ANNOUNCEMENTS)} detail pages, {total_pages} catalog pages under {SITE}")


if __name__ == "__main__":
    main()
