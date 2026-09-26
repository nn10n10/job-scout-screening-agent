# Job Scout Screening Agent

这是一个在本机运行的只读 Scout 初筛框架。`generic` 用虚构 fixture 演示完整流程；`green` 和 `type` 可通过已登录的 Windows Chrome 读取真实 Scout 与相关职位。Forkwell、LAPRAS、doda 等网页适配器尚未实现。不会自动登录、応募或发送消息。

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

## 扩展其他 Adapter

`forkwell`、`lapras`、`doda` 仍只会显示 `Adapter not implemented yet.`。后续应先人工登录并观察当前网页，再逐平台实现 `PlatformAdapter` 的 `is_logged_in`、`get_scout_list`、`get_scout_detail`、`normalize_scout`，把 URL 和 selector 建立在实际页面上。实现时只允许导航与读取。LinkedIn 不在此版本范围内。

## 安全与隐私

**READ-ONLY AUTOMATION**：禁止自动发送消息、応募、辞退、点赞、点击「興味あり」、修改资料或设置、删除消息、上传简历、下载敏感附件。程序不会伪装 `navigator.webdriver`、使用 stealth、修改 User-Agent、注入绕过检测的脚本，或绕过 OAuth 安全限制。浏览器打开后你的手工操作由你负责。不要把 Cookies、登录状态、API Key 或 Scout 私信提交到 Git：`.env`、`browser_profiles/`、数据库和 `output/` 已加入 `.gitignore`。报告和数据库本身包含职位与私信信息，只供本机查看。

## 测试

```bash
python -m pytest
```
