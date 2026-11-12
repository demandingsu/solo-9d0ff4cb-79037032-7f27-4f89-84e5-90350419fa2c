"""Pydantic 数据模型。"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class AnnouncementType(str, Enum):
    ORIGINAL = "招标公告"
    RESCHEDULE = "改期公告"
    WITHDRAWAL = "撤回公告"


class CollectRequest(BaseModel):
    catalog_url: HttpUrl = Field(..., description="公告目录第一页 URL")


class Announcement(BaseModel):
    """采集并解析后的单条公告（原始链路口径，不做失效判断）。"""

    announcement_id: str
    tender_code: str
    project_name: str
    type: AnnouncementType
    title: str
    published_at: str
    deadline: Optional[str] = None
    based_on_id: Optional[str] = None     # 改期公告所依据的公告 ID
    withdraws_id: Optional[str] = None    # 撤回公告所针对的改期公告 ID
    source_url: str


class CollectResult(BaseModel):
    catalog_url: str
    total_pages: int
    total_announcements: int
    collected_at: str
    announcements: list[Announcement]


# ---- 查询侧视图模型 ----

class AnnouncementView(BaseModel):
    announcement_id: str
    type: AnnouncementType
    title: str
    published_at: str
    deadline: Optional[str] = None
    based_on_id: Optional[str] = None
    withdraws_id: Optional[str] = None
    valid: bool = True
    invalid_reason: Optional[str] = None
    source_url: str


class ConflictItem(BaseModel):
    announcement_id: str
    deadline: str
    published_at: str
    source_url: str


class TenderSummary(BaseModel):
    tender_code: str
    project_name: str
    status: str  # ok | conflict | original_missing
    current_deadline: Optional[str] = None
    conflict: Optional[list[ConflictItem]] = None
    notes: list[str] = Field(default_factory=list)


class TenderDetail(TenderSummary):
    announcements: list[AnnouncementView]


class CollectResponse(BaseModel):
    status: str
    catalog_url: str
    total_pages: int
    total_announcements: int
    collected_at: str
    replaced_previous: bool


# ---- 双来源一致性核对 ----

class CrossCheckRequest(BaseModel):
    source_a: HttpUrl = Field(..., description="来源 A 招标网站公告目录第一页 URL")
    source_b: HttpUrl = Field(..., description="来源 B 招标网站公告目录第一页 URL")
    source_a_name: str = Field(default="来源A", description="来源 A 显示名称")
    source_b_name: str = Field(default="来源B", description="来源 B 显示名称")


class CrawlIssue(BaseModel):
    """单侧采集过程中出现的分页/详情问题（可追溯到具体 URL）。"""

    source: str = Field(..., description="来源标识：A 或 B")
    stage: str = Field(..., description="问题阶段：catalog（分页）或 detail（详情）")
    url: str
    error: str


class SourceCrawlSummary(BaseModel):
    source: str
    name: str
    catalog_url: str
    total_pages: int
    total_announcements: int
    truncated: bool = Field(..., description="目录分页是否因失败被截断")
    issues: list[CrawlIssue] = Field(default_factory=list)


class SourceAnnouncementRef(BaseModel):
    """配对中单侧公告的关键字段与来源链接。"""

    announcement_id: str
    type: AnnouncementType
    published_at: str
    deadline: Optional[str] = None
    based_on_id: Optional[str] = None
    withdraws_id: Optional[str] = None
    source_url: str


class FieldConflict(BaseModel):
    field: str = Field(..., description="字段标识：type/deadline/based_on_id/withdraws_id")
    label: str = Field(..., description="字段中文名")
    source_a: Optional[str] = None
    source_b: Optional[str] = None


class AnnouncementPair(BaseModel):
    """同一 (招标编号, 公告编号) 在两个来源间的配对核对结果。"""

    announcement_id: str
    tender_code: str
    type: Optional[AnnouncementType] = Field(
        default=None, description="公告类型（双方一致时给出；单方缺失时取存在侧）"
    )
    status: str = Field(
        ..., description="consistent | one_side_missing | conflict | uncertain"
    )
    needs_verification: bool = Field(..., description="受采集失败影响，需人工核实")
    version_index: int = Field(..., description="在本标段版本链中的序号（按发布时间，从 0 开始）")
    source_a: Optional[SourceAnnouncementRef] = None
    source_b: Optional[SourceAnnouncementRef] = None
    field_conflicts: list[FieldConflict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TenderCrossCheck(BaseModel):
    tender_code: str
    project_name: Optional[str] = None
    status: str = Field(
        ..., description="consistent | one_side_missing | conflict | uncertain"
    )
    needs_verification: bool
    pairs: list[AnnouncementPair] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CrossCheckResponse(BaseModel):
    status: str = Field(..., description="ok（两侧均完整）| partial（存在采集失败，保留已知对照）| failed（两侧均无可用数据）")
    sources: list[SourceCrawlSummary]
    total_tenders: int
    counts: dict[str, int] = Field(
        ..., description="各配对状态计数：consistent/one_side_missing/conflict/uncertain"
    )
    tenders: list[TenderCrossCheck]
    unresolved_failures: list[CrawlIssue] = Field(
        default_factory=list,
        description="无法归属到招标编号的详情页失败（其余失败已体现在待核实配对中）",
    )
    notes: list[str] = Field(default_factory=list)
    checked_at: str
