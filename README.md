# 市政工程投标公告采集 API

面向投标专员核对各标段**实际投标截止时间**的采集服务：接收公告目录 URL，沿分页抓取与内置样例结构一致的详情页，按招标编号归集**原公告 / 改期公告 / 撤回公告**，沿有效公告链计算当前截止时间；并行改期时间不一致时如实返回冲突，不猜测结果。

同时提供**双来源公告一致性核对 API**：输入同一项目在两个招标网站的目录 URL，分别沿分页抓取详情，按招标编号 + 公告编号配对、按发布时间形成版本链，逐字段核对公告类型、投标截止时间、依据公告与撤回关系，输出一致 / 单方缺失 / 内容冲突 / 无法判定四类结论；任一分页或详情失败都保留已知对照并将受影响标段标为**待核实**。

## 业务规则

1. **归集**：以招标编号分组。原公告为链根；每条改期公告通过“依据公告”注明其依据的公告（原公告或上一条改期）；撤回公告通过“撤回公告”字段指定被撤回的改期。
2. **撤回级联失效**：撤回指定改期后，该改期及其全部后续改期（沿依据链传递依赖）标记为失效并注明原因；失效公告不参与截止时间计算。
3. **有效链与截止时间**：从原公告出发，沿有效改期逐级下行，链末端公告的投标截止时间即当前有效截止时间，返回结果同时给出该公告的来源链接。
4. **并行改期冲突**：同一有效依据下存在多条有效并行改期时：
   - 截止时间一致 → 取该时间并在 `notes` 说明；
   - 截止时间不一致 → `status=conflict`，`current_deadline=null`，`conflict` 列出全部冲突公告及其时间与来源链接，**不猜结果**。
5. **重新采集**：再次采集成功时用完整新结果**整体替换**旧结果（临时文件 + 原子 rename）；抓取或解析任一页面失败时返回 502 且**不触碰存储**，旧结果完整保留。

## 目录结构

```
app/                 FastAPI 应用
  main.py            路由：采集、双来源核对、查询、离线样例静态挂载
  crawler.py         分页抓取（严格模式 collect + 容错模式 collect_partial）
  parser.py          目录页/详情页 HTML 解析（目录条目附带招标编号）
  chain.py           单来源归集、撤回级联失效、并行冲突判定
  crosscheck.py      双来源配对、版本链、字段级冲突与待核实判定
  storage.py         JSON 文件存储（异步锁、原子替换）
  models.py          Pydantic 模型
sample_site/         离线样例站点 A（相对链接、无外部资源）
  index.html
  catalog/page_1..3.html
  detail/A001..A018.html
sample_site_b/       离线样例站点 B（双来源核对专用，含冲突/单方缺失场景）
  index.html
  catalog/page_1..4.html
  detail/A001..A018（缺 A014）、B001、B002
scripts/generate_samples.py    样例站点 A 生成脚本（结构即解析契约）
scripts/generate_sample_b.py   样例站点 B 生成脚本
tests/               单元 + 集成测试（26 项）
Dockerfile / docker-compose.yml
```

## 快速开始（Docker Compose）

```bash
docker compose up --build
# API:       http://localhost:8000
# 交互文档:   http://localhost:8000/docs
# 离线样例 A: http://localhost:8000/sample  （也可直接用浏览器打开 sample_site/index.html）
# 离线样例 B: http://localhost:8000/sample-b
```

采集结果持久化在 `tender-data` 卷内（`/data/store.json`）。双来源核对为无状态计算，不落盘。

## 本地无 Docker 运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_samples.py          # 已随仓库提供，修改样例后重新生成
python scripts/generate_sample_b.py         # 生成第二套离线样例站点 B
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 接口

### `POST /api/collect` 触发采集

```json
// 请求
{ "catalog_url": "http://localhost:8000/sample/catalog/page_1.html" }
// 成功响应 200
{
  "status": "collected",
  "catalog_url": "...",
  "total_pages": 3,
  "total_announcements": 18,
  "collected_at": "2026-11-11T10:46:32.894225+00:00",
  "replaced_previous": true
}
// 失败响应 502（旧结果保留）
{ "detail": { "error": "抓取失败 ...", "previous_result_retained": true,
              "message": "采集失败，已保留上一次成功采集的结果" } }
```

### `POST /api/crosscheck` 双来源一致性核对

对两个目录分别做**容错采集**（分页/详情失败不中断），再按 `(招标编号, 公告编号)`
配对、按发布时间排序形成版本链，逐对比较 4 个字段：公告类型、投标截止时间、
依据公告（改期链路）、撤回关系（撤回目标）。

```json
// 请求
{
  "source_a": "http://localhost:8000/sample/catalog/page_1.html",
  "source_b": "http://localhost:8000/sample-b/catalog/page_1.html",
  "source_a_name": "招标网站A",
  "source_b_name": "招标网站B"
}
```

配对状态 `pairs[].status` 四选一：

| status | 含义 |
|---|---|
| `consistent` | 双方均存在且 4 个比对字段完全一致 |
| `one_side_missing` | 仅一方有该公告，无法交叉核对（备注注明缺失方） |
| `conflict` | 双方均存在但字段不一致，`field_conflicts` 逐字段列出双方取值 |
| `uncertain` | 双方详情均抓取/解析失败，无可用数据，无法判定 |

外层 `status`：`ok`（两侧完整）/ `partial`（存在采集失败，已保留已知对照）/
`failed`（两侧均无可用数据）。HTTP 状态码始终为 200，失败信息走 `sources[].issues`。

失败处理（不丢已知数据）：

- **详情页失败**：失败 URL 记录在 `sources[].issues`（`stage=detail`）；能从目录条目
  识别招标编号时，受影响配对/标段 `needs_verification=true`，其余配对正常出结论；
  无法归属的失败进入 `unresolved_failures`。
- **分页失败**：该来源 `truncated=true` 并停止翻页；因未读页面可能含任意标段公告，
  全部标段 `needs_verification=true`，已抓到的配对仍照常核对。

响应节选（内置样例的真实输出）：

```json
{
  "status": "ok",
  "total_tenders": 5,
  "counts": {"consistent": 15, "one_side_missing": 3, "conflict": 2, "uncertain": 0},
  "sources": [
    {"source": "A", "name": "招标网站A", "total_pages": 3, "total_announcements": 18,
     "truncated": false, "issues": []},
    {"source": "B", "name": "招标网站B", "total_pages": 4, "total_announcements": 19,
     "truncated": false, "issues": []}
  ],
  "tenders": [
    {"tender_code": "ZG-2026-001", "status": "conflict", "needs_verification": false,
     "pairs": [
       {"announcement_id": "A015", "status": "conflict", "version_index": 5,
        "field_conflicts": [
          {"field": "based_on_id", "label": "依据公告",
           "source_a": "A010", "source_b": "A005"}],
        "source_a": {"source_url": ".../sample/detail/A015.html", "...": "..."},
        "source_b": {"source_url": ".../sample-b/detail/A015.html", "...": "..."}}
     ]}
  ]
}
```

### `GET /api/tenders` 各标段截止时间汇总

```json
[
  { "tender_code": "ZG-2026-001", "project_name": "市政道路改造工程",
    "status": "ok", "current_deadline": "2026-12-04 09:30",
    "conflict": null, "notes": ["当前截止时间依据有效公告链末端公告 A018（改期公告）"] },
  { "tender_code": "ZG-2026-003", "status": "conflict", "current_deadline": null,
    "conflict": [ {"announcement_id": "A012", "deadline": "2026-12-05 09:30",
                   "published_at": "2026-11-12 09:00", "source_url": "..."},
                  {"announcement_id": "A013", "deadline": "2026-12-08 09:30", ...} ] }
]
```

### `GET /api/tenders/{招标编号}` 单标段完整公告链

每条公告带 `valid` / `invalid_reason` / `source_url`，可直接核对来源。例：

```
A011 改期公告 valid=False 被撤回公告直接撤回
A017 改期公告 valid=False 所依据的改期公告 A011 已失效（撤回级联）
```

其他：`GET /api/raw`（最近一次采集原始数据）、`GET /health`。

## 内置样例覆盖的场景

### 单站采集（`sample_site`，站点 A）

| 招标编号 | 场景 | 预期结果 |
|---|---|---|
| ZG-2026-001 | 原公告 + 4 次串行改期 | 截止时间取末次改期 `2026-12-04 09:30` |
| ZG-2026-002 | 第二次改期被撤回，第三次改期依赖它 | 撤回 + 级联失效，回退到第一次改期 `2026-11-26 09:30` |
| ZG-2026-003 | 同一依据下两条并行改期、时间不同 | `conflict`，列出 A012/A013 两个时间与来源 |
| ZG-2026-004 | 第一次改期被撤回、第二次级联失效 | 回退到原公告 `2026-11-21 09:30` |

### 双来源核对（A=`sample_site`，B=`sample_site_b`）

| 招标编号 | B 站相对 A 站的差异 | `/api/crosscheck` 预期 |
|---|---|---|
| ZG-2026-001 | A015 的依据公告为 A005（A 站为 A010） | 标段 `conflict`，A015 报 `based_on_id` 冲突及双方取值 |
| ZG-2026-002 | 无差异 | 全部配对 `consistent` |
| ZG-2026-003 | A013 截止时间为 `2026-12-09 09:30`（A 站为 `2026-12-08 09:30`） | A013 报 `deadline` 冲突 |
| ZG-2026-004 | B 站缺少撤回公告 A014 | A014 配对 `one_side_missing` |
| ZG-2026-005 | 仅 B 站存在（B001 原公告 + B002 改期） | 两条配对均 `one_side_missing`（A 侧为空） |

采集失败场景（分页中断、详情 500、双站全部不可用）由集成测试通过 MockTransport
模拟，验证已知对照保留与 `needs_verification` 标注，不预置损坏的静态文件。

## 页面结构约定（采集目标需与样例一致）

- 目录页：`<ul class="catalog">` 内每条一个详情 `<a href>`；分页用 `<a rel="next" href="...">`，无该链接即末页。
- 详情页：`<table id="announcement-meta">` 内 `th/td` 键值对，字段为
  公告编号、招标编号、公告类型（招标公告/改期公告/撤回公告）、项目名称、发布时间、
  投标截止时间、依据公告（改期必填）、撤回公告（撤回必填）。

## 测试

```bash
pip install -r requirements.txt
pytest -q
# 26 passed：
#   链路规则 7 项（含撤回级联、并行冲突、部分撤回）
#   单站 API 全流程 5 项（含采集失败保留旧结果、重新采集整体替换、离线样例可访问）
#   双来源核对单元 8 项（一致/四类字段冲突/双向单方缺失/详情失败待核实/
#     双侧失败无法判定/分页截断/无法归属失败/状态优先级）
#   双来源核对 API 集成 6 项（离线双站全流程、详情失败保留对照、
#     分页截断全标段待核实、双站全挂 failed、样例 B 静态可访问）
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATA_DIR` | `./data` | 采集结果存储目录 |
| `REQUEST_TIMEOUT` | `10` | 单次 HTTP 超时秒数 |
| `MAX_PAGES` | `100` | 目录分页上限（防循环/异常） |
| `MAX_CONCURRENCY` | `8` | 详情页抓取并发数 |
