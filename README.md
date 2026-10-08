# Job Scout Screening Agent

这是一个在本机运行的只读 Scout 初筛框架。`generic` 用虚构 fixture 演示完整流程；`green`、`type` 和 doda 的企业オファー可通过已登录的 Windows Chrome 读取真实 Scout 与相关职位。Forkwell 已提供主动 Search 适配；Forkwell / LAPRAS 的 Scout 消息适配器尚未实现。不会自动登录、応募或发送消息。

Forkwell 主动 Search 支持 `/jobs` 与 `/jobs/search?page=N` 广义列表增量扫描，复用本地筛选、AI 评价、缓存与人工状态。CLI 使用 `python -m scout_agent search forkwell`；WebUI 每次选择 Green、Forkwell、LAPRAS、Findy 或 Type 一个平台。关键词/职种筛选暂未接入。授权的真实环境可先手动打开搜索结果页及一个职位详情页，再运行
`python -m scout_agent.platform_discovery --cdp-endpoint http://127.0.0.1:9222 --platform forkwell`。
该 probe 只读取既有标签页，不导航、点击或刷新。JSON 包含 `page_kind` 候选、职位链接数量和脱敏路径、
分页路径与固定 query-key（`page`、`p`、`cursor`、`offset`）、next/prev/page-number 候选、
详情页 canonical 路径模式与 stable-ID segment 位置（从 1 开始，忽略空 segment），以及固定字段标题命中。
不输出真实 ID、公司名、正文或 query value；登录失效仍返回 `NEEDS_LOGIN`。
adapter 基于既有 probe 与 supervisor 提供的公开路由证据实现，使用数字职位 ID 和严格 URL 白名单；详情仅解析语义标题/字段，必须有同职位 canonical、职位标题和职责。缺字段、陌生路由、加载中、无有效链接均安全停止，不推定空结果。登录失效输出 `NEEDS_LOGIN`，只允许手动登录；不自动 OAuth、不回退 legacy 浏览器。本轮仅完成虚构离线验证，真实 DOM 解析与分页稳定性仍待授权 live 验收。

LAPRAS 已接入 Search adapter、CLI Search 和 WebUI，读取 `/jobs/home` 第一页及 numeric 职位详情。列表尚无 numeric 链接时，仅在同页最多等待 4 次、每次 500ms 并重新读取；耗尽仍安全停止，不推定空结果。详情结构合法但缺标题或职责时，同页最多重读 4 次，每次等待 500ms，并校验导航与同职位 canonical；耗尽才输出脱敏诊断并安全停止。document.title 单独存在不代表详情就绪。WebUI 隐藏 LAPRAS 分页数字字段，固定扫描第一页；live 验收待 supervisor 确认。

Type 已接入单页多 source Search：`python -m scout_agent search type`，默认扫描 `サーバ・クラウド（設計・構築）`（`https://type.jp/job-1/1004/22/`）和 `DevOps・SRE`（`https://type.jp/job-1/1006/161/`）两个已验证职种页的第一页。source navigation 仅接受当前 source 的 exact URL；`/job/search/` 仅为 search-entry。只采集 exact `/job-<category>/<job>_detail/` 并验证同路径 canonical，跨 source 按组合 identity 去重。WebUI 显示两个默认选中的 source checkbox、Type badge 和原始链接，隐藏分页字段；分页后缀尚未验证，不推进游标。Search recall-first 与 POLICY_VERSION 不变，真实 CLI / WebUI 验收仍待 supervisor。
在已登录 Chrome 中手动打开 LAPRAS 职位列表/推荐页及一个职位详情页后，使用
`python -m scout_agent.platform_discovery --cdp-endpoint http://127.0.0.1:9222 --platform lapras`。
无需提供真实职位 URL；probe 仅读取既有标签页，不导航、点击、刷新或登录。
输出 list/detail/other 候选、职位链接计数及路径模式、同职位 canonical 模式、数字 ID 段位置（从 1 开始）、
分页路径及固定 query-key、next/prev/page-number/load-more 候选、固定白名单字段标题及加载/安全失败状态。
`/jobs/:segment` 仅为路由候选，不认定 slug 是稳定 ID；load-more 只读取固定标签或链接候选，不点击。
真实 ID、公司名、JD、query value、个人资料和认证信息均不输出。登录失效保留 `NEEDS_LOGIN`，需手动登录。
仅在 LAPRAS numeric detail `/jobs/<id>` 上追加 `lapras_structure`：固定 label、tag（非白名单为 null）、固定 role/semantic_role、最近三层 ancestor、正文及下一个固定 label 的结构关系候选。
存在 exact 仕事内容/業務内容/職務内容 与 exact 概要 时，追加概要及 parent descriptor、immediate next sibling 的可见性/正文存在性/子节点数量桶，以及最多 8 个 direct child、12 个 DFS descendant 的脱敏结构；不输出正文、任意 heading 文本或 class/id/data-*。概要无 next sibling 时输出 null/空数组。
关系枚举为 same-parent-next-sibling / same-parent-following-sibling / ancestor-next-sibling / nested-next-block / tab-panel；候选不代表正式 parser selector。
title 只输出 `visible_h1_count`、`has_og_title`、`has_document_title`。未 exact-match「仕事内容」时，额外输出 normalized text 以该 label 开头的元素 tag 与 boolean。
不输出 title、正文、class/id/data-* 或任意属性值（固定 role 除外）；每页最多 100 个 label 和 100 个 prefix 元素。
本轮修复详情就绪重读，正式职责 parser selector 与边界规则未变。最新诊断仅出现 document.title，详情渲染竞态仍需 supervisor live 验收；分页未知时不猜 selector。

## 架构

`PlatformAdapter → Scout → SQLite 去重 → Mock/Codex/Gemini Classifier → Evaluation → HTML/JSON 报告`。
Playwright 负责确定性网页读取；LLM 仅分析职位文本，不控制浏览器。默认浏览器模式是 CDP：连接用户已打开的正式 Chrome。旧版 `launch_persistent_context()` 保留为显式选择的 fallback；它使用 `browser_profiles/scout/`，不会使用 Windows Chrome 的日常 Profile。

## 安装（WSL，Python 3.11+）

在本项目目录执行：

```bash
cd /path/to/scout-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m scout_agent --help
```

CDP 模式无需在 WSL 安装 Chromium，也不依赖 WSLg 图形环境。只有使用下文的 persistent fallback 时才需运行 `python -m playwright install chromium`。部分 WSL 发行版还需 `python -m playwright install-deps chromium`（可能要求 `sudo` 密码）。本环境已把缺少的 `libnspr4`、`libnss3`、`libasound2` 解压到 `.venv/chromium-deps/` 供 fallback 使用；重建虚拟环境时需重新安装系统依赖或这些库。

## 使用真实 Chrome + Google SSO

Google 可能阻止 Playwright 启动的 Chromium 登录。推荐使用 Windows 上的正式 Google Chrome，建立专用的 `Job Scout` 用户数据目录，**在 Chrome 中由你自己手工登录** Google 和招聘平台，然后让 scout-agent 连接这个已登录会话。程序不会启动 Windows Chrome，也不会输入密码或处理 2FA。

在 Windows PowerShell 中启动一个专用 Chrome 实例：

```powershell
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
& $chrome --remote-debugging-port=9222 --user-data-dir="$env:LOCALAPPDATA\JobScoutChrome"
```

如果 Chrome 安装在别处，请调整 `$chrome` 路径。`--user-data-dir` 必须指向**不同于 Chrome 默认目录**的专用目录；仅在默认 Chrome 中创建一个名为「Job Scout」的 Profile 不足以满足 Chrome 136 起的远程调试要求。参见 [Chrome 官方说明](https://developer.chrome.com/blog/remote-debugging-port)。请保持此窗口运行，并只在这个专用 Chrome 中完成登录。远程调试端口可控制该浏览器，不要将它暴露到公网或不可信网络。

在 WSL 的项目根目录创建 `.env`（可复制 `.env.example`），配置：

```dotenv
BROWSER_MODE=cdp
CDP_ENDPOINT=http://127.0.0.1:9222
```

先从 WSL 确认 endpoint 可访问：

```bash
curl -fsS http://127.0.0.1:9222/json/version
python -m scout_agent status
python -m scout_agent browser
```

`curl` 应返回带 `webSocketDebuggerUrl` 的 JSON。`browser` 只读取当前 tab 的标题和 URL，约两秒后断开 Playwright，**不会关闭 Chrome**，也不会创建或修改页面。如果端点不可达，会提示启用专用 Chrome 的 remote debugging，不会切换到 Chromium。Playwright 的 [CDP 连接说明](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)记录了该接口。

若 Windows 端可以访问 `http://127.0.0.1:9222/json/version`，WSL 端却不行，请检查 WSL 网络模式。默认 NAT 下，WSL 的 `127.0.0.1` 不一定指向 Windows 服务；支持的 Windows 11 环境可使用 mirrored networking，使两端能通过 localhost 相互访问。参见 [Microsoft WSL 网络说明](https://learn.microsoft.com/en-us/windows/wsl/networking#mirrored-mode-networking)。本项目不会自动更改 WSL 网络设置或开放调试端口。

## Persistent fallback（旧模式）

若需要旧模式，可在 `.env` 设置 `BROWSER_MODE=persistent`，并安装 Playwright Chromium：

```bash
python -m playwright install chromium
python -m scout_agent browser
```

此模式会启动 WSL 中的有界面 Chromium；需要 WSLg 或 X server。回到终端按 Enter 会关闭这个 Chromium，Cookies 和 localStorage 留在 `browser_profiles/scout/`。它只用于不依赖 Google SSO 的旧流程；请不要尝试在其中绕过 Google 登录限制。不要同时运行两个使用该 Profile 的浏览器实例。

## 运行 mock 扫描与报告

```bash
python -m scout_agent status
python -m scout_agent scan --platform generic
python -m scout_agent report
```

`generic` 从 `tests/fixtures/example_scout.json` 读取完全虚构的数据。分类器只由 `CLASSIFIER_PROVIDER` 选择；未设置时使用安全的 legacy 默认值 `mock`，不会根据 API Key 猜测。扫描自动创建 `data/scouts.db`，报告写入 `output/YYYY-MM-DD_HHMM_report.html` 和同名 JSON；同一分钟多次生成时添加数字后缀，保留旧报告。相同 Scout 已评估后不会重复处理，因此第二次扫描新增数量为 0。`report` 根据最近一次已完成扫描重新生成报告。

## 分类器与离线重新评价

推荐在项目根目录的 `.env` 中使用本机已登录的 Codex CLI：

```dotenv
CLASSIFIER_PROVIDER=codex
CODEX_MODEL=
CODEX_REASONING_EFFORT=low
CODEX_BATCH_SIZE=8
```

先运行 `codex login status`，确认输出为 ChatGPT 登录；若尚未登录，运行 `codex login`。Codex provider 不读取 OpenAI API Key；API-key 登录会被拒绝。`CODEX_MODEL` 留空时不传 `--model`，使用隔离运行下的 CLI 默认模型，报告标记为 `codex-default`；填写时通过 `--model` 传入所选名称。`CODEX_REASONING_EFFORT=low` 通过当前 CLI 支持的 `--config model_reasoning_effort="low"` 传入；留空则不指定。`CODEX_BATCH_SIZE=8` 表示最多 8 条 Scout 共用一次 `codex exec`。运行仍使用 `--sandbox read-only --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -C <临时目录>`，并禁用 shell/Web Search/app 工具。批量输出为 JSON 数组，逐个按内部 ID 校验；单条路径仍使用 `--output-schema`。参见 [OpenAI 的非交互模式说明](https://learn.chatgpt.com/docs/non-interactive-mode)及[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。它不会在项目目录运行，也不会使用 `--full-auto`。此操作会使用 Codex 登录状态所对应的额度。

另可明确选择 `CLASSIFIER_PROVIDER=mock`；或设置以下 Gemini 配置（Gemini 使用单独的 API billing）：

```dotenv
CLASSIFIER_PROVIDER=gemini
GEMINI_API_KEY=你的密钥
GEMINI_MODEL=你选择的模型名
```

`evaluate` 只读取 SQLite，不访问浏览器或招聘网站，也不会修改 Scout 原文。默认只评价尚无 Evaluation 的记录。对已有 Mock 结果，应先只读确认数量，再只替换 Mock；不要对已经有 Codex 结果的 Green 记录使用 `--force`：

```bash
python -m scout_agent evaluate
python -m scout_agent evaluate --platform green
python -m scout_agent evaluate --platform green --replace-provider mock --dry-run
python -m scout_agent evaluate --platform green --replace-provider mock
```

`--dry-run` 不调用模型、不创建 run、不修改 SQLite，只输出 `Selected Scouts: N`。正常运行每批成功后立即写库；若后续批次触及 quota、超时或返回无效 JSON，会停止并显示成功数、剩余数及安全的错误类别。某条 Scout 未出现在批量结果中时保留其旧评价。`--force` 仍可用于有意重评所有选中记录，但不能与 `--replace-provider` 同时使用。

没有显式选择 Gemini 时，不会因为 `GEMINI_API_KEY` 存在而切换 provider。Gemini 使用官方 `google-genai` SDK，以 Pydantic `Evaluation` 作为 structured output。数据库为每次评价记录 provider、model name、evaluated_at；HTML/JSON 报告也显示模型。筛选规则位于 `scout_agent/prompts/screening_rules.txt`；结果仍需人工复核。

KEEP 的必要条件是 Cloud/Infrastructure/Platform/DevOps/SRE 等属于岗位主要职责之一，不能仅凭 AWS、Terraform、CI/CD 等关键词判定。若 Web/Backend/Frontend、PM/Leader 或 Presales 是主责，Cloud/Infra 只是附带工作，通常判 MAYBE；有明确目标冲突才判 SKIP。Mock 使用保守的职位名称启发式，无法理解任意 JD 中的职责比例，因此结果必须人工复核；Codex/Gemini 会依据职位描述判断实际主责。

新评价的 `summary`、`reasons`、`concerns` 说明文字须为简体中文；公司名、职位名、技术术语保留原文。明显没有中文说明的模型输出会报错，不会写入数据库。旧评价不会自动改写；请用 `--replace-provider` 定向更新。

## Green 只读扫描

先按上文启动专用 Windows Chrome 并手工登录 Green，确保 `.env` 为 `BROWSER_MODE=cdp`，然后运行：

```bash
python -m scout_agent status
python -m scout_agent scan --platform green
```

Green 一览页是 `https://www.green-japan.com/messages/v2`。适配器从实际列表链接的 `threadId` 取得去重 ID；最多读取最近 30 条 Scout，遇到已处理 ID 即停止。只有新条目才会导航到消息详情与其中链接的职位页；新条目按显式选择的 provider 分类。已有 Scout 可用离线 `evaluate --platform green --replace-provider mock` 定向重评，无需再次访问 Green，也不会覆盖现有 Codex 评价。正文或接收时间在多消息线程中无法唯一对应时保存为 `null`；页面上没有可靠主题字段，因此 `scout_title` 也为 `null`。

## type 只读扫描

在专用 Chrome 中手工登录 type。首次验证建议临时用 MockClassifier，避免消耗 Codex 额度且不改动 `.env`：

```bash
CLASSIFIER_PROVIDER=mock python -m scout_agent scan --platform type
```

type 一览页为 `https://type.jp/scout/`。第一阶段只读列表，默认最多检查 100 条可见 message（`LIST_SCAN_LIMIT=100`），逐个独立职位标题做 `TITLE_SKIP`、`TITLE_REVIEW`、`DETAIL` 三档本地预筛。明显非 IT/非 Cloud-Infra 的职种直接跳过；Backend、社内 SE、Consulting、Security 等相邻岗位进入复核；Cloud/Infra/SRE 等明确相关或 `ITエンジニア`、`SE` 等泛化标题进入详情。同一 offer 已有明确核心岗位时，其泛化/相邻岗位可因 offer 上下文跳过。多个“還元率／案件選択制／単価連動”等案件信号可排除泛化 IT/SE/开发标题，但含 Cloud/クラウド、Infra/インフラ、SRE、DevOps、Platform、AWS、Network/ネットワーク、Server/サーバー 等目标信号的职位仍进入详情；SES/客先常駐风险只在读取 JD 后按明确证据做本地 hard rule 判断。列表没有可靠时刻，`received_on` 保存日期，`received_at` 保持 `null`。默认跳过超过 14 天的记录（`SCOUT_MAX_AGE_DAYS=14`）；连续 30 个已处理 message ID 才提前停止（`SEEN_STOP_THRESHOLD=30`）。当前观察到的列表没有分页控件，不追溯完整历史。

第二阶段仅为新候选打开 message 详情，通过已观察到的职位链接文本对应列表标题，只读取保留职位的职位页/JD；无法可靠匹配的链接保守读取，避免误跳过。独立 job ID 用 `offer/message ID + job ID` 保存为独立 Scout，不拼接不同 JD。只有真正送进 classifier 的 Scout 才消耗模型额度；已处理 message 不打开详情且为 0 token。每次扫描的 `scan_audit` 表逐职位保存 run ID、平台、external ID、公司、标题、决定、原因、是否读取详情和已见状态；列表阶段尚无 job ID 时使用明确的 `message ID:list:序号` 临时 ID，详情确认后换成 job ID。报告会列出列表、已见、三档预筛、详情和 KEEP/MAYBE/SKIP 计数。详情页只导航读取，新打开的详情可能自然变为已读；不会主动点击标记已读、応募、收藏或发送消息。`scout_kind` 可依据已观察到的详情链接区分；无明确依据时 `sender_kind`、`is_bulk_like` 为 `null`。

## doda 企业オファー只读扫描

在专用 Chrome 中手工登录 doda；首次扫描用 MockClassifier，避免消耗付费模型额度：

```bash
CLASSIFIER_PROVIDER=mock python -m scout_agent scan --platform doda
```

实际入口是 `https://doda.jp/dcfront/referredJob/interviewOfferList/`，不是公开的 `/scout/` 介绍页。扫描用只读 GET `?sort_id=1` 选择「受信日が新しい順」；网站默认 `sort_id=7` 是「マッチ順」。列表每张企业オファー卡片提供 `message_id` 和独立 `jid`；去重键是两者组合。最多读取最近 100 个职位，先用列表标题做 `TITLE_SKIP`／`TITLE_REVIEW`／`DETAIL` 预筛；只有候选才打开オファー详情和关联 JD。列表的宣传标题不一定等于实际职位名称，详情读取后会以 JD 的 `occupationName` 更新 audit。详情后的 `DETAIL_LOCAL_SKIP` 只使用真实职位标题和结构化 JD 主职责，在 hard rule/classifier 前以 0 token 排除明确非目标职种，以及明确以 Application/Web/System、組込/モビリティ、QA/Test、Helpdesk/技术支持或 IT 业务支援为主的岗位；泛化 IT 标题在职责仍不明确时保留。明确的 Cloud/Infra/Platform/SRE/DevOps 目标岗位不会因 SES 宣传词在这一步被排除。报告和 `scan_audit` 分别记录排除数量、公司、真实职位标题与原因，并保留原始 `list_title` 供复核。14 天年龄限制优先依据网站显示的「受信日から N 日経過」；列表只显示応募截止时间时，到详情复核接收年龄，仍无法确认年龄就不分类。连续 30 个已处理 ID 且没有待续跑条目时可提前停止。所有标题跳过和详情结果都记入 SQLite 与逐次 `scan_audit`，第二次扫描不重复分类。

目前只支持已观察到的「企業からのオファー」。可确认 `sender_kind=company`，带明确「プレミアムオファー」标签时记为 `premium_offer`；`is_bulk_like` 没有可靠证据时为 `null`。「パートナーエージェントからのスカウト」页面当前无可观察条目，暂不猜测其 DOM。详情导航可能自然变为已读，但不会主动执行已读、応募、收藏或其他账号操作。

已有完整 JD 的 doda Scout 可离线重新应用当前详情预筛与 hard rule，无需再次访问招聘网站或调用模型。先只读预览，再写入本地决定：

```bash
python -m scout_agent reprocess --platform doda --dry-run
python -m scout_agent reprocess --platform doda
python -m scout_agent evaluate --platform doda --eligible-only --replace-provider mock --dry-run
python -m scout_agent evaluate --platform doda --eligible-only --replace-provider mock
```

`reprocess` 只处理 SQLite 中已有非空 JD 的 Scout。`local_skip` 会更新原 Mock/legacy/local evaluation 为 `provider=local`，但不会覆盖 Codex/Gemini 结果；`classifier_candidate` 只记录资格，不改变已有 evaluation。`evaluate --eligible-only` 只读取这批候选，须显式指定平台，并继续遵守现有的 `--force`／`--replace-provider` 语义；推荐用 `--replace-provider mock` 精确替换历史 Mock 结果。`--dry-run` 不修改 SQLite，也不生成报告。

## マイナビ転職 企业 Scout 只读扫描

专用 Chrome 中手工登录后，首次验证使用 MockClassifier：

```bash
CLASSIFIER_PROVIDER=mock python -m scout_agent scan --platform mynavi
```

已观察到的企业 Scout 一览入口是 `https://tenshoku.mynavi.jp/scout/messages/`；网站分页链接使用 `order=2` 按最新接收顺序显示，每页 20 条。每条卡片提供接收日期、企业名、Scout 私信标题，以及指向一个 `jobinfo-...` 职位的链接。链接中的 `deliveryId` 与 `jobinfo-...` 职位 ID 共同形成去重键；若一张卡片以后出现多个独立职位链接，将逐职位拆分。列表没有独立的真实职位标题，因此不会把营销性质的私信标题误当职位标题做 `TITLE_SKIP`；标题未知时保守进入 `DETAIL`。详情页的职位名称、仕事内容、要求、薪资和地点用于本地 `DETAIL_LOCAL_SKIP`、hard rule 与后续 classifier。

扫描默认最多检查最近 100 个职位、只处理最近 14 天；连续 30 个已处理 ID 且无待续扫记录可提前停止。`scan_audit` 逐职位保存列表决定、详情决定、原因与是否读取详情；本地排除为 0 token，已处理职位不会再次分类。候选资格也保存在 SQLite，可供以后 `evaluate --platform mynavi --eligible-only --replace-provider mock` 精确重评。当前只实现页面明确标为「企業からのスカウト受信一覧」的企业列表；另有「転職エージェントからのスカウト」入口，但未将其混入企业列表。是否属于人工直邀或自动群发没有可靠 DOM 证据时为 unknown（`is_bulk_like=null`）。详情导航可能自然变为已读；程序不会主动标记已读、応募、キープ或辞退。

## 每日一键扫描

专用 Windows Chrome 已登录四个平台且开启 CDP 时，只需执行：

```bash
python -m scout_agent daily
```

按 Green → type → doda → マイナビ 的顺序增量扫描。已完成职位不会再打开详情；新职位先经过各平台现有本地筛选与 hard rule，只有未评价候选才交给 `.env` 指定的 `CLASSIFIER_PROVIDER`。Codex 会按 `CODEX_BATCH_SIZE` 跨平台批量评价；成功结果逐条持久化，未完成记录下次续跑。某个平台失败时其余平台继续，错误记录在统一的 HTML/JSON 每日报告中；返回码为非零表示存在部分失败。每日默认报告只展开 KEEP/MAYBE，SKIP 仅显示数量；原有逐平台 `scan_audit` 继续保留。

```bash
python -m scout_agent daily --dry-run
```

`--dry-run` 只读检查执行顺序及待续跑数量，不连接浏览器、不调用模型、不写数据库或评价，也不生成报告。`daily` 只支持 `BROWSER_MODE=cdp`，不会启动备用 Chromium。浏览详情可能自然变为已读；不会主动回复、応募、收藏或改动账号状态。

## 本地只读 WebUI

```bash
python -m scout_agent web
```

在本机打开 `http://127.0.0.1:8765`。顶部 Scout 筛选支持 Green / type / doda / マイナビ転職 多选，默认全选；“全部”仅本地勾选，“▶ 运行所选平台”异步触发所选平台的扫描、local reprocess 与 pending 分类（固定顺序），未选平台 pending 保持原样。运行中锁定选择并显示逐平台状态，运行中禁止重复启动；完成后自动刷新 SQLite 结果。状态仅保留在当前 Web 服务进程内，重启后回到待机，并只读显示数据库已记录的上次 daily 时间（历史摘要不可可靠恢复时留空）；不展示原始日志或异常。分页 URL 使用 `page` / `page_size`，筛选表单重置为第 1 页，越界页跳转到最后一页。请使用默认单进程 Web 服务，运行期间不要重启服务或同时从 CLI 启动 daily。Scout 人工状态独立于 AI evaluation：默认只显示 ACTIVE（未处理），可多选 APPLIED（已投递）/ EXCLUDED（已排除），URL 使用 repeated `status` 参数；缺省为 ACTIVE，空值或非法值返回 422。“全部”只勾选全部状态。行内“展开详情 / 收起详情”显示完整安全评价，原私信和完整 JD 不展示；`/jobs/{id}` 保留直链。每行状态按钮只写本地 `scout_user_states`，不会执行応募、回复或拒绝；daily 运行时返回 409。旧 DB 的 GET 不迁移，状态表只在人工状态 POST 创建，状态修改后刷新原 URL，保留筛选和分页。Dashboard 默认显示最近 7 天收到的正式评价（`Final`、KEEP/MAYBE），按 Tier S→A→B→C、同档接收日期从新到旧，以紧凑列表默认每页 10 条（可选 10/20/50/100）；可用 URL 查询参数筛选公司/职位关键词、平台、verdict、provider 与 1/7/14/30 天或全部时间，例如 `/?platform=type&verdict=MAYBE&days=7&q=infra`。筛选和顶部统计均在 SQLite 查询中完成，统计覆盖完整过滤结果，不受当前页影响。`Final` 不包含开发用 Mock；显式选择 `mock` 或 `All` 仍可查看，并会标为“测试/非正式评价”。接收日期缺失的 Green 记录只在 `days=all` 中出现；doda 使用首次观察日期与网站给出的天数推算。

紧凑列表显示公司、职位、薪资、地点、明确的 Remote/Hybrid 证据、接收日期、摘要及最多两条疑点预览；「筛选详情」显示已有评价的完整理由与疑点，但不展示原始 Scout 私信或 JD。安全的、已存储的 HTTP(S) 职位 URL 可在新标签页打开。

数据库不存在或没有匹配评价时显示空状态。浏览结果时仅只读查询数据库；手动触发 daily 后由现有流程读取招聘网站、运行已配置分类器并保存结果。WebUI 不提供応募、回复、收藏或修改历史评价的操作。服务器固定监听 `127.0.0.1`，不提供对外网络监听配置；无登录系统，请勿通过端口转发将其公开。

## 扩展其他 Adapter

`forkwell`、`lapras` 等仍只会显示 `Adapter not implemented yet.`。后续应先人工登录并观察当前网页，再逐平台实现 `PlatformAdapter` 的 `is_logged_in`、`get_scout_list`、`get_scout_detail`、`normalize_scout`，把 URL 和 selector 建立在实际页面上。实现时只允许导航与读取。LinkedIn 不在此版本范围内。

## 安全与隐私

**READ-ONLY AUTOMATION**：禁止自动发送消息、応募、辞退、点赞、点击「興味あり」、修改资料或设置、删除消息、上传简历、下载敏感附件。程序不会伪装 `navigator.webdriver`、使用 stealth、修改 User-Agent、注入绕过检测的脚本，或绕过 OAuth 安全限制。浏览器打开后你的手工操作由你负责。不要把 Cookies、登录状态、API Key 或 Scout 私信提交到 Git：`.env`、`browser_profiles/`、数据库和 `output/` 已加入 `.gitignore`。报告和数据库本身包含职位与私信信息，只供本机查看。

## 测试

```bash
python -m pytest
```

## GitHub supervisor 开发桥（V0.1）

`tools/agent_bridge.py` 是独立的开发编排器，不改变 Scout/classifier 行为，也不访问招聘网站。使用已安装并登录的本机 `gh` 与 Codex CLI；无需新增 PAT 或凭据文件。先确认 `gh auth status`、`codex login status`，并按上文安装 `.venv` 开发依赖。

```bash
python tools/agent_bridge.py --help
python tools/agent_bridge.py --issue 1 --inspect
python tools/agent_bridge.py --issue 1
```

`--inspect` 只读取 origin 对应仓库、issue/关联 PR 的评论及 marker ID，不调用 Codex、不写 state、不发评论。正常运行轮询间隔为 30 秒；可用 `--poll-seconds 15` 调整，`--once` 处理一个轮询批次后退出（它会执行任务，并非 dry-run）。`--python /path/to/python` 指定验证解释器，默认复用主 checkout 的 `.venv/bin/python`；`--timeout 1800` 设置 Codex/pytest 的超时秒数。

仅接受仓库 owner `nn10n10` 的评论，marker 必须位于正文开头：

- 控制 issue 中的 `[SUPERVISOR][TASK]` 启动/继续当前任务。
- issue 或关联 PR 顶层 conversation 中的 `[SUPERVISOR][REVIEW]` 在同一任务分支/worktree 上迭代；需要已有关联 PR。
- `[SUPERVISOR][APPROVED]` 仅在当前 open PR head SHA 等于最后独立验证并成功 push 的 SHA 时标记完成并退出；记录缺失或不一致时拒绝完成，需新的 REVIEW 重新验证。**不会自动 merge**。

桥从最新 `origin/main` 创建 `agent/issue-<N>`，在系统临时目录的 `scout-bridge-issue-<N>-*/repo` 中运行 `codex exec`；已有本地/远程分支与 open PR 会复用。若该分支已在其他 checkout 中使用，或对应 PR 已 closed/merged，会停止并提示，不抢占 checkout 或创建重复 PR。GitHub 评论始终作为 stdin prompt 数据传入，不直接作为 shell 命令执行。

开发 Codex 使用 `workspace-write`、`approval_policy="never"`，禁用额外 writable roots、全局 `/tmp`/`TMPDIR` 写权限、网络/Web Search/app 工具以及用户配置/rules。运行所需临时文件限制在任务 worktree 中，Git 元数据由 sandbox 保护。参见 [OpenAI sandbox 说明](https://learn.chatgpt.com/docs/agent-approvals-security)和[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。安装的 CLI 必须支持这些选项；失败时不会降级到更宽权限。分类器 Codex 仍为原来的只读隔离运行。

Codex 自行验证后，桥独立运行 `<主 checkout>/.venv/bin/python -m pytest`、`git diff --check` 和 staged diff 检查，cwd/PYTHONPATH 均指向 worktree。全部通过且有变更才 commit/push，并创建 main 为 base 的 PR，将链接与准确命令、退出码、pytest summary 发回 issue。控制 issue 不使用自动关闭关键词。Codex stdout/stderr 与退出码会捕获，原始模型输出不打印/持久化；失败验证的有界诊断仅经脱敏后输出到本机终端，issue 只收到命令/退出码/summary。敏感或生成文件不允许进入桥创建的 commit。

主 checkout 的 gitignored `.bridge-state/` 仅存 comment ID、worktree 路径、PR 信息和状态等元数据，不存指令正文/模型日志/测试日志。任务 worktree 内的 `.bridge-state/runtime/` 单独存放 Codex/pytest 临时产物，作为 `TMPDIR`，不会进入 commit。兼容旧版本误暂存的产物：独立验证前仅取消新添加的 `.bridge-state/`、`.bridge-tmp/`、`pytest-of-*` 和 `codex-bwrap-synthetic-mount-targets-*` 文件的暂存，保留文件与全部源码修改；此前已 tracked 的产物仍拒绝发布。三个 diff 检查继续执行。失败命令的具体 diff 参数、退出码及脱敏后最多 1500 字符的诊断显示在本地终端，GitHub 不接收原始 stdout/stderr 或任意 argv。

仓库共享的文件锁保证一次只有一个桥运行，覆盖所有 issue 与 linked worktree。comment ID 在调用 Codex **之前**原子写入；重启不会重复执行。若 Codex/测试失败，或 Ctrl-C 中断任务，桥等待新的 owner marker 评论；同一评论不会重试。GitHub 通知失败会保留状态并在下一次轮询重试；在远端已发出评论而本地未收到响应的极端情况下，状态通知可能重复，但 Codex 不会重复运行。`--once` 失败返回 1，Ctrl-C 返回 130。

worktree 会保留供 review/失败排查。APPROVED 后可从主 checkout 用 `git worktree remove <记录的 worktree 路径>` 清理，并保留 `.bridge-state/` 的已处理 ID。不要删除 state 后重启同一控制 issue，否则旧 TASK 会再次成为待处理指令。未执行真实付费 Codex 的自动集成测试；单元测试中的 `gh`、`git`、`codex` 均为 mocks。


### Green Search V0.1（TASK-003）

```bash
python -m scout_agent search green --probe --max-jobs 5
python -m scout_agent search green --max-jobs 30 --max-model-jobs 20
python -m scout_agent search --help
```

主动搜索候选池与 Scout 筛选分开：Search 使用 `source_kind=search` 的独立
`search_jobs` / `search_evaluations` SQLite 表，不更新已有 Scout 或 Codex Evaluation。
默认来源：AWS、SRE、DevOps、Terraform、Kubernetes 的 `/search/skill/<label>`，
以及 インフラエンジニア 的 `/jobtype-l/190150/01`。`--keyword` 仅接受这些 source labels，
matched keyword 如实记录来源。クラウドエンジニア / Platform Engineer 是内容匹配目标，
由上述来源覆盖，不表示精确关键词搜索。
可重复传入 `--keyword`；`--pages-per-keyword` 默认 2。最多读取 30 个独立职位详情，
最多送 20 个职位给 Codex，复用 `CODEX_BATCH_SIZE`（默认 8），固定 low reasoning。
TARGET + POSSIBLE 达到 15 时可提前停止（批次边界可能略超出阈值）。

漏斗顺序为列表本地排除 → 结构化 JD → 内容/策略缓存 → 详情 hard rule → Codex batch。
缓存命中无需重复本地或模型判断；首次运行详情 hard rule 仍在模型之前执行。
明显无关标题、明确年收上限低于 450 万、明确以 SES/客先常驻为主、纯 Helpdesk/监控
或明确纯应用开发可零 token 排除。模糊 Infra/IT/社内SE 保守进入详情；
Kubernetes/EKS 或 ArgoCD/Istio/observability 缺口不单独导致 DROP。
Remote 等未提供字段显示 UNKNOWN。无法确定的职责/条件保留 POSSIBLE；
TARGET 必须有主要基础设施职责证据。模型预算耗尽的职位保持待处理，不伪造评价。

紧凑 JD 仅包含公司、职位、年收、地点/Remote、主职责、必須、歓迎、技术；
模型不接收整页 HTML、导航/footer 或重复字段行。全局按公司 ID + job ID 去重，
合并实际遇到的 matched keywords。按 stable job ID + content_hash + policy_version
永久保留历史缓存，仅 JD 内容或策略版本改变时重新评价。
报告默认展示 TARGET + POSSIBLE，DROP 仅计数；Web UI `/search` 默认显示 ACTIVE + TARGET / POSSIBLE + 全部平台，支持人工状态、verdict、平台三维多选过滤（维度内 OR，维度间 AND）。使用重复 query 参数保存选择，例如 `status=ACTIVE&status=APPLIED&verdict=TARGET&verdict=POSSIBLE`；推荐、全部和全部平台仅即时修改当前勾选，不跳转或提交，最后点击“应用筛选”统一提交；无 JS 时可手动勾选并提交。快捷选择不是业务值；空值或非法值返回 422，计数覆盖完整候选池。默认每页 10 条，可选择 10 / 20 / 50 条；page_size 保存到 URL，筛选和翻页保留每页条数，按 TARGET → POSSIBLE → DROP、最近 last_seen_at（NULL 在后）、job_id 排序。已应用的过滤状态保存在 URL 中，应用筛选回到第 1 页；越界页码重定向到最后一页，空池回到第 1 页。扫描范围仅使用友好展示标签，真实 source value 和游标保持原值；运行详细统计默认折叠。
最终统计包括原始卡片、独立职位、本地排除、缓存、送模型职位、批次及各分类数量。

**代码已启用，但自动测试不代表 live verified。首次使用前建议先运行 `--probe`。**
probe 仅连接用户已有已登录 Chrome，每来源只检查第一页，默认/最多读取 5 个独立
职位详情；不创建/迁移 SQLite、不生成报告、不加载模型 provider 或调用 classifier。
只输出 source、卡片/合法链接数量、title/salary/location 和主职责可解析数量，
不打印公司、正文、JD 或 URL。至少有合法链接且一个详情能解析 title + responsibilities
才返回 0；否则非零退出，请检查 CDP/登录/source 页面或更新 DOM 解析器。
开发与自动验收均只使用 fictional mocks，**live probe 尚需用户本机执行**。

列表仅读取 visible job anchors，严格验证 same-origin HTTPS 职位路径；使用既有
段落解析器，缺字段保持 UNKNOWN。分页第一页使用 base URL，后续使用 `?page=N`。
无合法链接时只读取明确的结果数量文本：确认 0 件返回空；正数或未知布局安全失败。
详情仅读取 h1 和已确认的 `仕事内容` 区域；缺标题或职责不会送模型。
若 Green DOM 变化，runtime safety check 非零退出，不回退为全页抓取。
只允许 CDP，不自动 fallback 到 persistent Chromium。禁止応募、気になる、面谈、收藏、
消息、账号设置、上传或自动応募。此版本不支持其他平台主动搜索。

虚构 50 卡片回归样例：25 个列表排除、10 个详情排除、15 个进入 Codex，
批次为 8 + 7；重复运行 25 个详情缓存命中、0 个送模型、0 次模型批次。
这些是 fictional mocks 的结果，不是 live Green 验证或模型 quota 使用记录。

Search 结果保存 provider、model_name 与 evaluated_at（UTC）；本地规则标记为
`local` / `local-rule`。旧缓存的未知模型和判断时间保留为空，不编造历史元数据。
可确认的年收范围、固定年收与上限以 JPY 保存 salary_min / salary_max；
月给、时给及不确定文本保持 UNKNOWN，报告保留原始薪资。
模型配额、认证、超时或格式错误返回非零退出码，并保留已成功结果；
失败批次不保存评价，剩余候选可下次续跑。


Green probe 失败时只输出 source label、有限的 stage/reason code 和结构数量，
不输出职位内容、公司、URL 或 Playwright 原始错误。`CDP_CONNECT` 表示连接阶段；
`SOURCE_NAVIGATION` / `SOURCE_URL` 表示来源导航或不安全跳转；
`JOB_LINKS` / `RESULT_COUNT` 表示链接或结果数量结构异常；
`DETAIL_NAVIGATION` / `DETAIL_TITLE` / `DETAIL_RESPONSIBILITIES` 表示详情导航、标题或主职责解析失败。
`PLAYWRIGHT_TIMEOUT` / `PLAYWRIGHT_ERROR` 表示对应阶段的浏览器读取失败。
无结果的 source 会继续下一个来源，总详情预算仍不超过 5；布局安全异常立即非零退出。
导航目标始终由白名单 source route 生成。允许同来源路径的 canonical query 或末尾斜线，
也允许已登录 Chrome 中观察到的同源 HTTPS `/search` 加非空 query 的 canonical redirect；
不记录或输出 query 内容。第 2 页及以后必须确认最终 query 的 `page` 与请求页码一致，
缺失或冲突时安全停止。仍拒绝跨域、HTTP、userinfo、空 query 的 `/search` 及其他路径。
自动测试通过不代表 live verified；live probe 尚需用户本机执行。


### TASK-004：Green 增量新岗发现（V0.1）

默认 `search green` 每个 source 固定扫描 page 1，再轮转 2 个深页：
首次 1 + 2 + 3，下次 1 + 4 + 5。`--coverage-pages 2` 控制深页数量，
`--max-depth 15` 控制最大页码（至少 2），扫描到上限或明确空页后从 page 2 重启。
AWS、SRE、DevOps、Terraform、Kubernetes、インフラエンジニア 分别保存 cursor。
成功完成页面及其模型批次后保存 cursor，失败页面下次重试；详情/模型预算不会阻止其他 source 的 page 1 扫描。
深页有 NEW 职位因详情预算不足而未保存时，该页不推进 cursor，也不继续该 source 后续深页；下次从该页重试。page 1 始终按每轮重扫处理。
深页中已保存详情但因模型预算不足而未获得评价的 NEW／KNOWN 职位也会保留当前 cursor，并停止该 source 后续深页；下次直接使用已保存详情续跑模型，不重复读取详情。其他 source 的 page 1 仍会扫描。
不使用未经验证的新着排序参数，网站操作仍仅为读取。

显式 `--pages-per-keyword N` 保持旧模式：固定前 N 页、读取详情并按 content hash 复用评价，
不推进轮转 cursor。未指定该参数才启用增量模式。
增量模式已见 job_id 更新 last_seen 和 matched source，复用保存的详情/评价；
失败或预算待处理的模型评价直接使用保存的详情续跑。历史 first_seen 保持 NULL，不能据此推测首次发现日期。
预算内尚未读取详情的卡片不入库，下次覆盖该页时再尝试。

报告优先 NEW TARGET、NEW POSSIBLE，KNOWN 已有评价只计数，未完成评价的 KNOWN 可显示续跑结果。
统计包含 pages_scanned、source_pages（每 source 实际成功读取的列表页）、new_jobs、known_jobs、cache_hits、model_jobs、deferred 以及每 source cursor before → after。CLI 和报告显示如 `AWS pages: 1,2,3`，便于连续运行验收；列表读取成功不代表该页详情覆盖已完成。
**本轮未实现自动刷新旧 JD，不自动检测内容变化或 UPDATED/重新出现事件；KNOWN 表示曾入库，不表示职位没有变化。**
需要检查旧 JD 时可显式使用旧页数模式；该模式仍按内容 hash 复用缓存。
筛选保持 recall-first，不收紧投递规则。

Search 不稳定或首次运行时可配置较小 `CODEX_BATCH_SIZE=2`；全局默认不变。
Codex 失败仅输出安全 category，不输出 JD/raw stderr。成功批次保留，失败批次下次续跑。
代码验证只使用虚构数据和 mocked 模型；真实验收由用户本机已有 Chrome CDP 连续两次小预算 AWS Search 完成，
确认 page 1 固定、深页前进、KNOWN 不重复大量送模型。真实验收前不发送 `[SUPERVISOR][APPROVED]`，Bridge 不 merge。

### WebUI 增量 Search（Green / Forkwell / LAPRAS）

安全停止时，本轮状态显示 `failed_source`、`failed_page` 与固定枚举的
`safe_reason`，用于定位最后进入的 source/page；这不表示该页已处理完成。
诊断不会显示原始异常、URL query 或 JD，仍保持 fail-closed。

启动 `python -m scout_agent web`，打开 `http://127.0.0.1:8765/search`。
勾选已验证 source 后点击“开始增量搜索”；默认 coverage_pages=2、max_depth=15、
max_jobs=30、max_model_jobs=20。高级参数中的 Codex batch size 默认 2，仅影响该次子进程。
需要已登录且可连接的 Chrome CDP；招聘网站操作仍然只读。

页面轮询后台状态，同一进程只允许一个 Search run；重启后运行状态回到 idle。
“本轮搜索状态/统计”展示安全的当前 source/page 进度，以及计数和 pages/cursor（CLI 完成时输出）；
“当前候选池”以单列横向列表展示持久化历史评价，默认折叠，窄屏自动堆叠。
展开详情可查看本地 AI 摘要、理由、风险及结构化 JD；展开不会访问 Green。
顶部可切换待处理（默认）/ 已投递 / 已排除 / 全部，并显示各状态数量；
AI 排序仍为 TARGET → POSSIBLE → DROP，人工状态与 AI verdict 独立。

人工状态仅写本地 SQLite 的独立 `search_job_user_state` 表，不向 Green 投递、
拒绝、收藏或回复。状态按钮只在展开后显示，首次点击仅显示确认，取消不写入。
排除或标记已投递后职位从待处理移走，可在对应过滤视图恢复到待处理；
刷新、重启和重新扫描不会重置状态。搜索运行时禁止人工状态写入。
历史职位没有状态记录时视为待处理，普通 GET 不执行 migration；
首次明确状态 POST 或 writable SearchStore migration 才会创建本地状态表。
NEW 0、KNOWN 增加也是正常增量结果。失败只显示安全类别，可稍后重试并复用已有缓存。
服务仅绑定 127.0.0.1，POST 要求页面随机 CSRF token，不自动打开浏览器。


LAPRAS active Search 已实现：`python -m scout_agent search lapras`，WebUI 可选择 LAPRAS / 求人検索。当前仅扫描已 live 验证的 `https://lapras.com/jobs/home` 第一页，不推进来源游标。唯一稳定 identity 是 numeric `/jobs/<id>`，保存为 `lapras:<id>`；slug link 暂不持久化，无 numeric 职位时安全停止。详情要求同 numeric canonical、title 与职责白名单字段；分页尚未验证，不生成 page/cursor URL，后续取得真实分页证据后再扩展。复用缓存与本地人工状态，POLICY_VERSION 不变。本次隔离开发不做 live 浏览器验证。


Findy active Search 使用已验证的推荐列表与分页：`python -m scout_agent search findy`，WebUI 来源为 `おすすめ求人`，每轮深页数与最大扫描页可编辑。职位身份为 `findy:<company_id>:<job_key>`，游标为 `findy:おすすめ求人`，独立于其他平台。仅接受 strict Findy detail URL；列表与详情各最多同页等待 4×500ms，身份、canonical、登录或页面结构异常立即安全停止。详情仅解析可见 h1 与固定语义字段，不使用正文兜底。POLICY_VERSION 与历史评价保持不变。

本次实现仅完成虚构数据离线验证；CLI / WebUI live 验收及 supervisor APPROVED 仍待授权环境完成。

Findy Stage B.1 仅补充只读 detail structure diagnostic，正式职责 parser 暂未修改。缺失 title / responsibilities 时，CLI 向 stderr 输出一条 `Findy safe detail diagnostic: <sanitized JSON>`；WebUI 忽略该行，不保存到 summary/state。输出仅含固定 labels、tag/role、最多三层 ancestor、固定关系枚举与 `仕事内容` 后续节点的脱敏 shape，不含正文或职位身份。诊断失败保留原有安全停止原因。

手动 probe 可使用 `python -m scout_agent.platform_discovery --cdp-endpoint http://127.0.0.1:9222 --platform findy`：只 evaluate 已打开标签页，仅 exact `/companies/<numeric>/jobs/<opaque>` 附加同一脱敏结构，不导航或自动登录。等待 supervisor review 后，由用户在授权环境运行同一小预算 CLI 获取 live diagnostic；桥不做 live。


doda 主动 Search：`python -m scout_agent search doda --keyword インフラエンジニア`。首版仅使用已验证的广义职种来源与 `-page__N/` 分页，WebUI 默认勾选该来源并显示分页设置、doda badge 和经身份校验的外部链接。候选身份 `doda:<jid>`、游标 `doda:インフラエンジニア` 与 Scout/其它平台隔离，沿用 recall-first、缓存及人工状态，POLICY_VERSION 不变。详情仅从 NEXT_DATA 结构化字段提取，route/canonical/jid 不一致或缺少标题、职责时安全停止；不复用 Scout 严筛。本次仅虚构数据验证，真实 CLI、缓存复跑和 WebUI 验收待 supervisor。

マイナビ転職 当前仅支持 Stage A.1 只读 discovery，尚未接入主动 Search adapter。由用户手动登录并打开搜索结果、普通求人详情及第二页后，可在授权环境运行：

```bash
PYTHONPATH="$PWD" .venv/bin/python -m scout_agent.platform_discovery \
  --cdp-endpoint http://127.0.0.1:9222 --platform mynavi
```

输出仅包含固定结构：exact `/jobinfo-:id4/` 与 `/job/:segment/` anchor 计数、allowlist tracking key presence、脱敏 source shape、final `/pg<digits>/` 分页候选以及普通详情 DOM bool。只有 current 与 same-origin canonical 的四段 ID 完全一致，才报告 `jobinfo-id4` 身份候选；该候选尚未用于 production。登录、加载或快照异常 fail-closed；不输出真实 ID、criteria、query value、公司、职位或 JD。Bridge/tests 仅用虚构数据，live 结果须等待 supervisor review 后再确定下一阶段。

マイナビ転職 主动 Search：`python -m scout_agent search mynavi --keyword インフラエンジニア`。仅启用已验证的 `/engineer/list/o166/` 来源及 `/pgN/` 分页。职位身份 `mynavi:<四段ID>`，游标 `mynavi:インフラエンジニア`；tracking query/fragment 不保存。详情仅读取固定局部 selector，current/canonical 身份漂移、登录失效或缺少标题/职责时安全停止。保持 recall-first、缓存、人工状态和 POLICY_VERSION，不复用 Scout 严筛。WebUI 支持来源恢复、分页设置及经身份验证的 マイナビ転職 外链。本次仅虚构数据验证，真实验收待 supervisor。
