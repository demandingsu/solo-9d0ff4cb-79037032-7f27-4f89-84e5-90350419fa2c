"""公告采集器：沿目录分页抓取详情并解析。

- collect：严格模式，任何网络或解析错误都抛出 CrawlError，由上层决定“保留旧结果”。
- collect_partial：容错模式，分页/详情失败不中断，保留已成功抓取的公告并记录
  每个失败点（stage/url/error 及可归属的招标编号），供双来源核对标注“待核实”。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from . import config
from .models import Announcement
from .parser import CatalogEntry, ParseError, parse_catalog_entries, parse_catalog, parse_detail


class CrawlError(Exception):
    pass


@dataclass
class FailedEntry:
    stage: str          # catalog | detail
    url: str
    error: str
    tender_code: str | None = None


@dataclass
class PartialCrawlResult:
    announcements: list[Announcement]
    pages: int
    truncated: bool                 # 目录分页是否因失败被截断
    failures: list[FailedEntry] = field(default_factory=list)


class Crawler:
    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(config.MAX_CONCURRENCY)

    async def _get(self, client: httpx.AsyncClient, url: str) -> str:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise CrawlError(f"抓取失败 {url}: {exc}") from exc
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise CrawlError(f"响应不是 HTML {url} (content-type={ctype})")
        return resp.text

    async def _fetch_detail(self, client: httpx.AsyncClient, url: str) -> Announcement:
        async with self._sem:
            html_text = await self._get(client, url)
        try:
            return parse_detail(html_text, url)
        except ParseError as exc:
            raise CrawlError(str(exc)) from exc

    async def collect(self, catalog_url: str) -> tuple[list[Announcement], int]:
        """沿分页抓取全部详情。返回 (公告列表, 目录页数)。

        全部成功才返回；任一页面失败即抛 CrawlError，不返回部分结果。
        """
        async with httpx.AsyncClient(
            timeout=config.REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": config.USER_AGENT},
        ) as client:
            detail_urls: list[str] = []
            seen_pages: set[str] = set()
            page_url: str | None = catalog_url
            pages = 0

            while page_url is not None:
                if page_url in seen_pages:
                    raise CrawlError(f"目录分页出现循环: {page_url}")
                seen_pages.add(page_url)
                pages += 1
                if pages > config.MAX_PAGES:
                    raise CrawlError(f"目录页数超过上限 {config.MAX_PAGES}，疑似分页异常")

                html_text = await self._get(client, page_url)
                try:
                    urls, page_url = parse_catalog(html_text, page_url)
                except ParseError as exc:
                    raise CrawlError(str(exc)) from exc
                detail_urls.extend(urls)

            # 去重（不同目录页理论上不会重复，保险处理）
            detail_urls = list(dict.fromkeys(detail_urls))
            results = await asyncio.gather(
                *(self._fetch_detail(client, u) for u in detail_urls)
            )

        by_id: dict[str, Announcement] = {}
        for ann in results:
            if ann.announcement_id in by_id:
                raise CrawlError(f"公告编号重复: {ann.announcement_id}")
            by_id[ann.announcement_id] = ann
        return list(by_id.values()), pages

    async def collect_partial(self, catalog_url: str) -> PartialCrawlResult:
        """容错采集：任一分页或详情失败都保留已知结果并记录失败点。

        - 目录页失败：停止翻页（truncated=True），已发现的详情继续抓取；
        - 详情页失败：记录失败（尽量带招标编号），不影响其余详情；
        - 公告编号重复：记录为问题并保留先到的一条。
        """
        failures: list[FailedEntry] = []
        truncated = False
        async with httpx.AsyncClient(
            timeout=config.REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": config.USER_AGENT},
        ) as client:
            entries = []
            seen_pages: set[str] = set()
            page_url: str | None = catalog_url
            pages = 0

            while page_url is not None:
                if page_url in seen_pages:
                    failures.append(
                        FailedEntry("catalog", page_url, "目录分页出现循环，已停止翻页")
                    )
                    truncated = True
                    break
                seen_pages.add(page_url)
                if pages + 1 > config.MAX_PAGES:
                    failures.append(
                        FailedEntry(
                            "catalog", page_url,
                            f"目录页数超过上限 {config.MAX_PAGES}，已停止翻页",
                        )
                    )
                    truncated = True
                    break

                try:
                    html_text = await self._get(client, page_url)
                    page_entries, page_url = parse_catalog_entries(html_text, page_url)
                except (CrawlError, ParseError) as exc:
                    failures.append(FailedEntry("catalog", page_url, str(exc)))
                    truncated = True
                    break
                pages += 1  # 仅统计成功解析的目录页
                entries.extend(page_entries)

            # 去重（不同目录页理论上不会重复，保险处理）
            unique: dict[str, CatalogEntry] = {}
            for e in entries:
                unique.setdefault(e.url, e)
            entries = list(unique.values())

            async def _one(entry: CatalogEntry) -> Announcement | FailedEntry:
                try:
                    async with self._sem:
                        html_text = await self._get(client, entry.url)
                    return parse_detail(html_text, entry.url)
                except (CrawlError, ParseError) as exc:
                    return FailedEntry("detail", entry.url, str(exc), entry.tender_code)

            settled = await asyncio.gather(*(_one(e) for e in entries))

        announcements: list[Announcement] = []
        by_id: dict[str, Announcement] = {}
        for item in settled:
            if isinstance(item, FailedEntry):
                failures.append(item)
                continue
            if item.announcement_id in by_id:
                failures.append(
                    FailedEntry(
                        "detail",
                        item.source_url,
                        f"公告编号重复: {item.announcement_id}，已保留先采集的一条",
                        item.tender_code,
                    )
                )
                continue
            by_id[item.announcement_id] = item
            announcements.append(item)

        return PartialCrawlResult(
            announcements=announcements,
            pages=pages,
            truncated=truncated,
            failures=failures,
        )

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
