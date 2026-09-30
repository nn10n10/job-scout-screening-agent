# Job Scout Screening Agent

这是一个在本机运行的只读 Scout 初筛框架。`generic` 用虚构 fixture 演示完整流程；`green`、`type` 和 doda 的企业オファー可通过已登录的 Windows Chrome 读取真实 Scout 与相关职位。Forkwell、LAPRAS 等网页适配器尚未实现。不会自动登录、応募或发送消息。

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

在本机打开 `http://127.0.0.1:8765`。Dashboard 默认显示最近 7 天收到的正式评价（`Final`、KEEP/MAYBE），按接收日期从新到旧、最多展示 100 条；可用 URL 查询参数筛选公司/职位关键词、平台、verdict、provider 与 1/7/14/30 天或全部时间，例如 `/?platform=type&verdict=MAYBE&days=7&q=infra`。筛选和顶部统计均在 SQLite 查询中完成，统计不受 100 条卡片上限影响。`Final` 不包含开发用 Mock；显式选择 `mock` 或 `All` 仍可查看，并会标为“测试/非正式评价”。接收日期缺失的 Green 记录只在 `days=all` 中出现；doda 使用首次观察日期与网站给出的天数推算。

卡片显示公司、职位、薪资、地点、明确的 Remote/Hybrid 证据、接收日期、摘要及最多两条疑点预览；「筛选详情」显示已有评价的完整理由与疑点，但不展示原始 Scout 私信或 JD。安全的、已存储的 HTTP(S) 职位 URL 可在新标签页打开。

数据库不存在或没有匹配评价时显示空状态。WebUI 不连接招聘网站、不运行模型、不提供応募、回复、收藏或修改评价的操作，也不会创建数据库。服务器固定监听 `127.0.0.1`，不提供对外网络监听配置；无登录系统，请勿通过端口转发将其公开。

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


本机 CLI 兼容性证据（2026-09-30）：实际执行 `codex exec --help`，退出码 0；help 列出当前调用使用的 `--sandbox`（支持 `workspace-write`）、`--ephemeral`、`--ignore-user-config`、`--ignore-rules`、`--color`、`--config`、`-C` 及 stdin `-`。本环境提示无法创建 PATH aliases（Read-only file system），但 help 正常返回。此检查未调用模型。help 仅确认 flags 与 TOML override 语法，不能证明 `approval_policy`、`allow_login_shell`、`web_search`、`sandbox_workspace_write.*`、`apps._default.enabled`、`agents.enabled` 的运行时效果；这些 config 与实际隔离效果仍待首次授权的真实 bridge run 验证，测试不会为此调用付费模型。发布成功后 state 保存 `verified_published_sha`，旧 state 无此字段时不能直接 APPROVED，需先 REVIEW。


本次 publish 修复 REVIEW 的本地验收证据（供外层 bridge 更新 PR #2）：

- 精确复现：`git diff --cached --check` 退出码 2，指出 `pytest-of-zmang/pytest-0/` 下虚构 HTML 报告的 trailing whitespace。`git diff --cached --name-only -z` 退出码 0。根因是旧版本把 worktree 根目录当作 TMPDIR，发布的 `git add --all` 将测试报告、pytest 链接和 Codex sandbox 临时产物一并暂存；不是 git flag 不兼容。
- `TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1 /home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest tests/test_agent_bridge.py -q`：退出码 0，`75 passed in 0.39s`。相关测试文件 `tests/test_agent_bridge.py`；新增覆盖旧误暂存产物恢复、已 tracked 产物拒绝发布、publish staged check 失败、本地诊断脱敏及限长、临时目录外部链接拒绝。原有中文输出、APPROVED 绑定 verified SHA、review 分支/PR 复用和不 merge 测试保留。
- `TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1 /home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest`：收集 401 项，在 `tests/test_web.py` 首项阻塞，Ctrl-C 中断，退出码 130，无最终 summary。最终代码再次执行 `TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=3 20 /home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest`：收集 402 项，同处阻塞，退出码 137，无最终 summary；不能报告完整套件通过。
- `TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=3 20 /home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest tests/test_web.py::test_web_empty_state_does_not_create_database -vv -o faulthandler_timeout=5`：退出码 137，无最终 summary；5 秒堆栈显示主线程等待 AnyIO portal，后台线程等待 asyncio selector。最小本地 asyncio 线程唤醒实验显示回调仍在 ready 队列，内部 socket 的 send 返回 `PermissionError: [Errno 1] Operation not permitted`；沙箱禁止该 IPC 写操作。未更改 WebUI、测试或安全权限以绕过限制。
- `TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1 /home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest --ignore=tests/test_web.py`：退出码 0，`385 passed in 4.57s`；排除 17 项 WebUI 测试，仅作诊断，不替代完整验收。
- `git diff --check`：退出码 0，无输出。旧暂存区中的生成文件未由开发 Codex取消暂存（禁止修改 Git 元数据），`git diff --cached --check` 的原始失败仍保留给外层 bridge；更新后的 bridge 会在验证前按限定路径恢复暂存区，而不丢弃文件或源码修改。
- `/home/zmang/scoutfilter/scout-agent/.venv/bin/python tools/agent_bridge.py --help`：退出码 0，输出“由仓库 owner 控制的 GitHub/Codex 本地开发桥；不访问招聘网站。”和中文选项说明。
- `codex exec --help`：退出码 0，实际 help 确认当前 flags，提示无法创建 PATH aliases（Read-only file system），详见上文。未调用模型；config 的运行时隔离效果仍待授权验证。
- warning/skip/failure：通过的两组测试均无；完整套件及单项 WebUI 无最终 summary，存在上述阻塞。未访问真实浏览器、真实数据库、招聘网站、网络或付费模型；gh/git/codex 测试调用均为 mocks。subagent 未运行（当前环境无该工具）。

下一步由外层 bridge 加载更新后的实现，在现有 worktree 恢复误暂存产物并独立执行完整验证；仅全部通过后提交、推送到同一 `agent/issue-1` / PR #2。此开发任务未 commit、push、修改 Git 元数据、发送 GitHub 消息或 merge。完整套件的沙箱 IPC 阻塞和 config 运行时验证仍未闭环。

本轮补充验证（保留上轮全部修改）：

- 新增 `test_pytest_generated_files_are_ignored_while_source_is_committed`：mock pytest 在真实 TMPDIR 中写入虚构 HTML，依据仓库 ignore 规则模拟 staging，确认只提交源码，且 staging 后、commit 前仍执行 `git diff --cached --check`。新增 `test_force_staged_runtime_output_is_rejected_before_commit`：模拟强行暂存 runtime 文件，确认拒绝 commit/push。所有 gh/git/codex subprocess 均 mock；这不是实际 Git staging 集成验证。
- `TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1 /home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest tests/test_agent_bridge.py -q`：exit 0，`77 passed in 0.41s`。
- `TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=3 20 /home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest`：收集 404 项，在 `tests/test_web.py` 阻塞；exit 137，无最终 pytest summary。
- `TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1 /home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest --ignore=tests/test_web.py`：exit 0，`387 passed in 4.83s`。仅作诊断，不代替完整套件。
- `git diff --check`：exit 0，无输出；`git diff --cached --check`：exit 2，旧 `pytest-of-zmang/pytest-0/` HTML 的 trailing whitespace 仍在暂存区。开发 Agent 未修改 index；外层 bridge 必须先执行限定路径的恢复逻辑再验证。
- `/home/zmang/scoutfilter/scout-agent/.venv/bin/python tools/agent_bridge.py --help`：exit 0，输出中文桥说明及参数帮助。`codex exec --help`：exit 0，再次确认上述 flags；唯一 warning 是 PATH aliases 无法创建（Read-only file system），未调用模型。
- 通过的测试无 warning/skip/failure；完整套件超时，暂存区检查失败。真实浏览器、真实数据库、招聘网站、网络、付费模型访问：none；subagent：not run（当前工具不可用，按任务要求自行验证）。仍未 commit/push/merge；需外层 bridge 完成独立验证后发布同一分支/PR。


本轮修复：验证前与 publish 入口均先恢复新增的历史临时暂存文件并验证暂存路径；git add 后再次验证，最后执行 git diff --cached --check。无法安全恢复的已 tracked 产物在 whitespace 检查前拒绝，并在本地输出脱敏路径。新增回归测试覆盖 .bridge-tmp 历史 staging、直接 publish 恢复、已 tracked 临时文件提前拒绝。不会关闭 whitespace 检查或自动 merge。

本轮验证（开发 sandbox 内自行执行；subagent 工具不可用）：

- 测试命令共同前缀：`TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1`；解释器 ` /home/zmang/scoutfilter/scout-agent/.venv/bin/python`。
- `-m pytest tests/test_agent_bridge.py -q`：exit 0，`80 passed in 0.39s`，无 warning/skip；相关新增测试为 `test_historical_tracked_artifact_rejected_before_whitespace_check`、`test_publish_directly_recovers_historical_staged_artifacts`，并扩展旧 staged 产物恢复测试。gh/git/codex 均 mock。
- `-m pytest -q`：TestClient 阻塞后中断，exit 130，无最终 pytest 摘要。再次 `timeout 40s <解释器> -m pytest -q -o faulthandler_timeout=15`：exit 124，无最终摘要；堆栈定位 `tests/test_web.py:63` 的 `test_web_empty_state_does_not_create_database`，Starlette TestClient / AnyIO portal 等待。
- `-m pytest -q --ignore=tests/test_web.py`：exit 0，`390 passed in 4.31s`，无 warning/skip。此结果不替代全套验收。
- `git diff --check`：exit 0，无输出。`git diff --cached --check`：exit 2，旧 `pytest-of-zmang/pytest-0/` HTML trailing whitespace；本次明确禁止修改 git metadata，故未清理 index。
- `<解释器> tools/agent_bridge.py --help`：exit 0，输出“由仓库 owner 控制的 GitHub/Codex 本地开发桥；不访问招聘网站。”，列出 issue/python/once/inspect 参数。
- 真实 `codex exec --help`：exit 0，支持 `--sandbox workspace-write`、`--config`、`-C`、stdin `-`、`--ephemeral`、`--ignore-user-config`、`--ignore-rules`；警告 PATH aliases 因 read-only filesystem 无法创建。未运行真实 codex exec 任务。
- 真实浏览器、真实数据库、招聘网站、网络、付费模型访问：none；未 commit/push/merge 或发送 GitHub 消息。外层 bridge 须先加载当前修复、恢复历史 staging，再完成全套与 cached whitespace 检查，全部通过后发布 PR #2。


最新 REVIEW 验证（2026-09-30，保留既有源码/index 修改）：

- 已删除 worktree 根目录残留 `pytest-of-zmang/`、`codex-bwrap-synthetic-mount-targets-1000/`；`.bridge-tmp/` 不存在。未修改 index 或其他 Git 元数据；index 中旧新增产物仍需外层 bridge 的限定恢复逻辑处理。
- 发布防护补充检查所有路径层级的 pytest/bridge 临时目录与 Python 缓存，并拒绝 `.venv`。相关回归：`tests/test_agent_bridge.py::test_force_staged_runtime_output_is_rejected_before_commit`（四种路径）与 `test_sensitive_or_external_files_cannot_be_published`。
- 测试共同环境前缀：`TMPDIR="$PWD/.bridge-state/verification" PYTHONDONTWRITEBYTECODE=1`。`/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest tests/test_agent_bridge.py -q`：exit 0，`84 passed in 0.40s`，无 warning/skip/failure。
- 同环境 `timeout --signal=INT --kill-after=3 20 /home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest -o faulthandler_timeout=5`：exit 137，collected 411 items，无最终 summary；阻塞在 `tests/test_web.py` 首项 TestClient，5 秒诊断显示主线程等待 AnyIO portal，后台线程等待 asyncio selector。
- 同环境 `/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest --ignore=tests/test_web.py -q`：exit 0，`394 passed in 4.61s`，无 warning/skip/failure；仅诊断，不替代完整验收。
- `git diff --check`：exit 0，无输出。`git diff --cached --check`：exit 2，index 中历史 `pytest-of-zmang/pytest-0/` HTML trailing whitespace；删除磁盘文件不改变 index。
- `/home/zmang/scoutfilter/scout-agent/.venv/bin/python tools/agent_bridge.py --help`：exit 0，关键输出“由仓库 owner 控制的 GitHub/Codex 本地开发桥；不访问招聘网站。”，中文参数说明。
- `codex exec --help`：exit 0，再次确认当前 invocation 所用 flags，唯一 warning 为 PATH aliases 创建失败（Read-only file system）；无模型调用，运行时隔离尚未在真实任务中验证。
- subagent：not run（工具不可用，按任务要求自行验证）。真实浏览器、真实数据库、招聘网站、网络、付费模型访问：none。gh/git/codex 单测调用均 mock。
- 未 commit/push/merge、变更分支或发送 GitHub 消息。下一步外层 bridge 恢复历史新增临时文件 staging，并在支持 TestClient 的环境独立验证；全部通过后更新现有 agent/issue-1 / PR #2。
