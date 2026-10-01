# TASK-007：多平台 Search 基础与调查阻塞

本轮隔离开发指令禁止浏览器、招聘网站、网络、真实数据库和付费模型。
因此未进行 live 调查，未选择或启用第二个平台；TASK-007 尚未完成。
用户已打开浏览器不等于当前隔离执行获得浏览器访问权限。

## 六个平台调查状态

按任务指定顺序记录。下表不是 live 调查结果；未知项不得据此实现 selector。

| 平台 | 搜索入口/路由 | 登录要求 | 分页/游标 | 稳定 ID/URL | 列表字段 | 详情字段 | SPA/CDP 稳定性 | 主动 Search 适用性 | 复杂度/风险 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Forkwell | 未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 无 DOM 证据，无法评估 |
| Findy | 未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 无 DOM 证据，无法评估 |
| LAPRAS | 未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 无 DOM 证据，无法评估 |
| type | Scout adapter 已存在，Search 入口未调查 | 未确认 | 未确认 | Scout 代码含职位路径，Search 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | Scout 支持不等于 Search 支持 |
| doda | Scout adapter 已存在，Search 入口未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | Scout 支持不等于 Search 支持 |
| マイナビ転職 | Scout adapter 已存在，Search 入口未调查 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | 未确认 | Scout 支持不等于 Search 支持 |

六个平台当前均在 Search 入口层 fail-closed：没有 CLI/UI 可执行适配器。
后续调查只允许已有 Chrome/CDP 的导航和读取，缺登录记录 needs-login，不自动登录。
只记录计数、路由模式和安全分类，不保存真实 JD、个人资料或认证信息。

## 最小适配层

`search_platforms.py` 提供共享 Job 与 SearchAdapter Protocol：platform_key、
source_labels、source_url、ensure_verified、validate_job、search_cards、job_detail。
字段复用既有列表/详情字段。Green 实现该接口，并在 funnel 中验证职位 URL/ID。
Green 的 safe stage/category 诊断保持原样；新平台需基于 live 证据实现自己的
安全诊断，不能输出原始异常、query、cookie 或页面内容。
分页沿用整数深页 cursor；如实测平台只有 opaque cursor，需要再增量扩展 contract。
现有 fake adapter 的默认 Green 兼容路径保留。

## 身份、存储与缓存兼容

Green 保留 `company_id:job_id`，新平台使用 `platform:external_id`。
搜索来源 Green 保留旧 label，新平台使用 `platform:label`，防止 AWS/SRE 游标碰撞。
SearchStore 仅在显式迁移时 additive 增加可空 platform/external_job_id；不回填
无法确认的历史平台。已观察的 Green 职位在保存时写入 metadata。
GET /search 仍使用 migrate=False，并兼容旧表缺少新列的情况。
主键、evaluation、content_hash、first_seen/last_seen 和人工状态语义不变。

保留 `green-search-0.1.1` 作为既有 policy 的缓存键：名称有历史原因，但规则没有
改变，因此不全量重评、不消耗真实模型额度。以后只有实际规则变化才升级 policy。

UI 增加平台 badge 和动态外链文案；当前仍只执行 Green，未添加未验证的平台选项。
新平台完成只读调查和验证后再提供严格 allowlist 的单平台选择，保留单 run 锁，
不在此阶段启用 all 或并发执行。

## 后续验收

由具备明确只读浏览器授权的 supervisor 环境完成六平台调查，再选择一个平台。
仓库 AGENTS 的实现顺序优先 type；仍须以 Search DOM 证据决定是否安全可行。
随后补充新 adapter fictional 列表/详情及 URL fail-closed 测试、CLI/UI 选择与
配置持久化测试，并完成两页列表、少量详情、缓存 pipeline 的授权 live 验证。
本轮没有新平台解析器，也没有新平台 live 或付费模型验证，不能宣称已交付第二平台。

## 手动 platform discovery probe

这是基础设施准备；第二平台仍待 live DOM 证据，TASK-007 未完成。
Bridge 不访问真实浏览器。用户在本机已登录 Chrome 中手动打开六个平台的
搜索结果页，然后在自己的终端运行：

```bash
python -m scout_agent.platform_discovery --cdp-endpoint http://127.0.0.1:9222
```

默认顺序为 Forkwell / Findy / LAPRAS / type / doda / マイナビ転職。
可重复使用 `--platform type --platform doda` 缩小范围。
输出仅到 stdout；若保存，应重定向到本地临时文件，不提交仓库。
退出码 0 表示每个已检查页面都有候选链接证据；1 表示存在安全失败分类，
不代表平台不可适配，也不代表任何 selector 已获验证。

probe 仅连接本机 CDP，读取既有标签页，不新建页面、不导航、不点击、
不刷新、不关闭用户页面、不登录、不启动 persistent Chromium。
仅允许源码 PLATFORMS 中的六组固定 HTTPS 域名，其他标签页不执行读取。
用户必须自行打开搜索结果页；缺少标签页输出 NO_OPEN_TAB。
只使用通用 DOM 结构读取，不推断平台专用 selector。

输出包括脱敏 route/path、分页参数候选、候选 job link pattern、数字路径 ID 候选、
固定白名单中的可见 section heading/字段标签、readyState、busy 和 SPA marker。
路径中的任意文本都替换为 :segment，数字替换为 :id；不输出 query 值、
fragment、真实 ID、链接文本、公司名、个人资料、JD 正文、cookie 或 token。
候选链接仅依据通用 job/jobs/detail 路径词汇，未知模式不猜测。
分页与 SPA 都只是单次快照候选；不能证明分页可用、稳定 ID 或 SPA 行为。
页面变化、读取失败、登录表单、加载中分别输出安全分类；不输出原始异常。

用户本机运行后，根据脱敏结果决定后续人工 DOM 调查与第 2 平台实现，
仍需补充真实结构证据及专用 adapter 测试。本轮不实现第二平台 selector。

## 验证记录说明

上一轮 Bridge 报告 643 passed，属于历史验证记录，不是本轮 probe 的验证。
旧的开发阶段 timeout 记录已移除，避免与 Bridge 最终结果混淆。
本轮验证命令及结果见交付摘要；真实浏览器/数据库/招聘网站/付费模型均未访问。

本轮主 Agent 验证（无可用 subagent）：
- `/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest -q --basetemp=.pytest-probe-tmp tests/test_platform_discovery.py tests/test_search_platforms.py tests/test_green_search_discovery.py`
  退出码 0，`129 passed in 0.66s`；failed/skipped/xfailed/warnings：none。
  probe focused tests 覆盖固定域名、脱敏、安全失败分类、页面变化、只读调用、
  endpoint 校验及 mocked CDP 命令入口。临时测试文件已清理。
- 同一 Python 执行 `-m scout_agent.platform_discovery --help`，退出码 0；
  显示六平台选项及“不导航、不登录、0 模型调用”。
- `git diff --check` 退出码 0，无输出。
- live 与真实数据库验证：not run；真实浏览器/招聘网站/付费模型访问：none。

## Issue #13 补充：登录失效处理

probe 在读取链接/字段之前检测可见密码框、登录表单、登录或 session expired
标题/按钮（包括 Google SSO 登录按钮）；只返回布尔值，不保留认证页面文本。
已打开的平台登录路由直接输出 NEEDS_LOGIN，不读取页面。读取过程中跳转到
平台登录路由或 accounts.google.com 时，即使读取抛异常也归类为 NEEDS_LOGIN，
不继续读取职位证据或详情；仍继续其他平台。不会点击登录按钮或完成 OAuth。

stdout 保留原有脱敏页面证据 JSON；stderr 每个平台输出一行 JSON 汇总：
OK / NEEDS_LOGIN / BLOCKED / UNSUPPORTED。NEEDS_LOGIN 优先于同平台其他
标签页的成功状态，附带中文手动登录提示。OK 仅表示发现候选链接，不等于
已验证 adapter。UNSUPPORTED 表示缺少可归属的标签页证据，不表示网站没有
Search 功能。BLOCKED 表示加载中、读取失败或缺少候选等安全阻塞。

如果标签页在 probe 启动前已经停留在 Google 域名，无法可靠确定所属平台；
不会读取 Google 页面或根据 OAuth query 推断平台，此时对应平台可能输出
UNSUPPORTED。需人工恢复平台页面后再调查。通用检测不能证明已登录，
未来 adapter 必须补充经真实 DOM 验证的认证证据；本轮不猜测平台 selector。

WebUI telemetry 支持 allowlist 平台的 NEEDS_LOGIN 汇总，丢弃原始 message，
显示独立中文登录提示。当前 WebUI 仍仅运行 Green；这只是后续平台接入的
安全类别准备，不代表第二平台已接入或登录态已获 live 验证。

本轮禁止浏览器/网络，未调查 live DOM，未启用第二平台。下一步由获授权的
supervisor 先调查 type Search，再按 AGENTS 顺序选择可落地的平台。

### 本次补充验证（主 Agent，无可用 subagent）

精确命令：

```bash
/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest -q --basetemp=.pytest-login-final-tmp tests/test_platform_discovery.py tests/test_search_platforms.py tests/test_green_search_discovery.py tests/test_web_search.py -k 'not test_get_csrf_validation_and_single_run and not test_invalid_request and not test_safety_stop_context_redaction_and_reset'
```

退出码 0；摘要 `148 passed, 16 deselected, 1 warning`，failed/skipped/xfailed：none。
warning：`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
focused tests：`test_login_redirect_is_not_domain_or_parse_failure`、
`test_login_page_stops_before_snapshot`、
`test_redirect_during_read_stops_and_continues_other_platforms`、
`test_login_dom_discards_all_job_evidence`、
`test_summary_one_status_per_platform_and_login_precedence`、
`test_needs_login_summary_is_a_distinct_safe_ui_category`。
DOM 测试使用 mocked snapshot，未执行真实浏览器 JavaScript。

首次完整 Web 用例运行在 TestClient 用例停滞后中断（退出码 130，无最终
pytest summary），当次还包含已修复的 sign_in 路由识别失败。
因此排除上述 TestClient 用例对应的 16 个 case；未宣称完整 Web 回归通过。

CLI smoke：`/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m scout_agent.platform_discovery --help`，
退出码 0；stdout 含六个平台选项和“不导航、不登录、0 模型调用”。
`git diff --check` 退出码 0，无输出。
真实浏览器/数据库/招聘网站/付费模型访问：none；live 验证：not run。
