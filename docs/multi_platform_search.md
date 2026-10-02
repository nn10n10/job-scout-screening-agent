# Issue #13：多平台主动 Search

第二平台选择 Forkwell。依据为已有脱敏 live probe 的数字职位路径、canonical
与字段标题候选，以及 supervisor 补充的 Forkwell Jobs 官方公开结构证据。
Forkwell 有主动 Search 能力，不属于仅推荐/浏览平台。本轮隔离开发禁止网络和
浏览器，未重新调查，也不宣称真实解析或分页已通过验收。

| 平台 | 已有证据 / 可行性 | 本轮范围 |
| --- | --- | --- |
| Forkwell | `/jobs`；关键词与详细条件；Cloud/Infra、SRE、Platform 职种；年收、雇用形态、地点、Remote、自社サービス；公开第二页 `/jobs/search?page=2` | 优先实现广义列表，过滤参数留待后续 |
| Findy | 未获得足够 Search DOM / 路由证据 | 不启用 |
| LAPRAS | 未获得足够 Search DOM / 路由证据 | 不启用 |
| type | Scout adapter 已有，尚无足够 Search 证据 | 不启用 Search |
| doda | Scout adapter 已有，尚无足够 Search 证据 | 不启用 Search |
| マイナビ転職 | Scout adapter 已有，尚无足够 Search 证据 | 不启用 Search |

## 接入与安全边界

`SearchAdapter` 复用既有 pipeline。Green 来源保持原名与历史 `company_id:job_id`；
Forkwell 仅一个 `求人一覧` source，使用 `forkwell:求人一覧` 游标和
`forkwell:<数字 ID>` 职位身份，不影响 Green cache、evaluation 或人工状态。
保留 `green-search-0.1.1` policy key，因为评价规则没有改变。

Forkwell 仅允许 `https://jobs.forkwell.com`；详情支持已观察的 `/jobs/<数字>`
及 `/<公司路径>/jobs/<数字>`。丢弃 query/fragment，canonical 必须指向当前
同一职位与路径。列表第一页 `/jobs`，后续 `/jobs/search?page=N`；当前严格
校验最终来源 URL。职位链接去重后进入 recall-first 本地/详情/AI 筛选。
列表字段没有可靠值时保持未知，详情依靠 `h1`、语义标题及 `dt/th` 字段解析，
职责缺失安全停止，不能用 AWS 等技术关键词代替职责。

只调用 goto/evaluate，读取可见链接、语义字段；不读取 body 作兜底，
不点击、填表、上传、投递、表达兴趣或更改账户。密码框、登录表单/标题、
平台登录路径或 Google SSO 重定向返回 NEEDS_LOGIN，未自动登录。
加载中、无有效链接、陌生路由、canonical/ID 不匹配均 fail-closed；
无有效链接不等于已证实零结果或分页终点。

CLI `python -m scout_agent search forkwell` 与 WebUI 单平台选择复用运行锁、
预算、去重、缓存、人工状态及结果列表。旧 UI 请求和 session 配置默认 Green。
Forkwell 不使用 Green `--probe`；结构 probe 仍为 `platform_discovery`，仅读取
用户已有 CDP 标签页。平台内关键词/职种过滤不在本轮范围。

## 验收与限制

新增 `tests/test_forkwell_search.py` 只用虚构 snapshot、mock 模型及临时 SQLite，
覆盖 URL allowlist、数字 ID、分页、重复链接、登录跳转、安全停止、详情解析、
WebUI 平台启动/外链，以及缓存复用和 Green 人工状态/游标保持。
另有 WebUI 配置持久化与完整回归验证，精确命令及结果见交付摘要。
本轮主 Agent 执行验证，当前工具没有 subagent 能力。

真实浏览器、真实数据库、招聘网站及付费模型访问：none；live 验证：not run。
后续由获授权的 supervisor 环境只读验收两页列表、少量详情、两轮缓存复用，
确认语义字段结构和 CDP 稳定性后再优化平台内过滤；Bridge 不做 live 浏览器访问。

### 本轮验证命令

普通 `python -m pytest -q` 全量尝试在既有 Starlette TestClient 的
`anyio.from_thread` / selector 跨线程唤醒等待处停滞，退出码 130，无最终摘要。
随后在测试进程内临时限制 EpollSelector 单次等待到 50ms，让同一套全量测试
正常执行；不跳过用例，不修改应用、测试断言或安装的依赖，不开放网络。
此测试调度补丁仅存在于下面进程中，不能算普通 pytest 命令在该 sandbox 中通过。

```bash
/home/zmang/scoutfilter/scout-agent/.venv/bin/python - <<'PY'
import selectors
import pytest
original = selectors.EpollSelector.select
def bounded_select(self, timeout=None):
    return original(self, 0.05 if timeout is None else min(timeout, 0.05))
selectors.EpollSelector.select = bounded_select
raise SystemExit(pytest.main(['-q', '--basetemp=.pytest-offline-safety-final']))
PY
```

直接运行的 focused 验证：

```bash
/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest -q --basetemp=.pytest-forkwell-safety tests/test_forkwell_search.py tests/test_search_platforms.py
/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m scout_agent search --help
git diff --check
```

focused 退出码 0，`36 passed in 0.33s`；failed/skipped/xfailed/warnings：none。
CLI smoke 退出码 0，stdout 包含 `{green,forkwell}`、`单次选择 Green 或 Forkwell`
与 `求人一覧`；未实际启动 Search。diff 检查退出码 0，无输出。

最终全量退出码 0，`715 passed, 1 warning in 36.29s`；failed/skipped/xfailed：none。
warning 为既有 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
全部模型调用均 mock，SQLite 仅虚构临时 fixture，真实浏览器/数据库/招聘网站/付费模型访问 none。


LAPRAS active Search 已实现：`python -m scout_agent search lapras`，WebUI 可选择 LAPRAS / 求人検索。当前仅扫描已 live 验证的 `https://lapras.com/jobs/home` 第一页，不推进来源游标。唯一稳定 identity 是 numeric `/jobs/<id>`，保存为 `lapras:<id>`；slug link 暂不持久化，无 numeric 职位时安全停止。详情要求同 numeric canonical、title 与职责白名单字段；分页尚未验证，不生成 page/cursor URL，后续取得真实分页证据后再扩展。复用缓存与本地人工状态，POLICY_VERSION 不变。本次隔离开发不做 live 浏览器验证。


### LAPRAS 阶段 A 定向证据与阶段 B 边界

Supervisor 最新 live 证据确认 source 为 `/jobs/home`，32 个 job links，
路径类型为 `/jobs/:id` 与 `/jobs/:segment`；numeric_path_segment 是唯一稳定 ID。
旧 probe 将 home 脱敏并误判为 detail；修复后固定显示 `/jobs/home` 并识别为 list。
观察到 `page` query key candidate，但没有 verified pagination controls/link evidence，
因此仍不启用分页。`/jobs/search` 是此前硬编码导航遗留 tab，不作为 source 证据，也不导航它。
Numeric detail canonical 为同 `/jobs/:id`，稳定 ID 位于路径 segment 2；
标题命中 勤務地 / 給与 / 開発環境 / 雇用形態。Slug detail 无稳定路径 ID 或 canonical，
因此不能用于持久化身份。职责仍需命中固定 allowlist，否则 fail-closed。

本次真实浏览器、真实数据库、招聘网站、网络、付费模型访问：none。
新增测试使用 fictional DOM / snapshot、mock classifier 与 worktree 内临时 SQLite。
直接 focused 命令：
`/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest -q --basetemp=.pytest-lapras-focus tests/test_lapras_search.py tests/test_web_search_config.py::test_lapras_config_survives_reload`
退出码 0，`34 passed, 1 warning in 0.59s`。
随后补充 canonical、title、GET 不 migration、CLI source 与安全遥测用例；
最终全量使用前述仅测试进程 bounded_select 方案，参数为
`pytest.main(['-q', '--basetemp=.pytest-lapras-final-full'])`。
普通全量在现有 TestClient 跨线程等待处中止（退出码 130，无最终摘要），
此 workaround 不代表普通 pytest 在 sandbox 中通过。
CLI smoke：`/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m scout_agent search --help`，
退出码 0，stdout 包含 `{green,forkwell,lapras}` 和来源 `求人検索`；未实际启动 Search。
`git diff --check` 退出码 0，无输出。

最终全量退出码 0：`791 passed, 1 warning in 41.46s`；failed/skipped/xfailed：none。
定向最终验证同一 bounded_select 方案运行 `tests/test_lapras_search.py tests/test_web_search_config.py`，
退出码 0：`44 passed, 1 warning in 2.41s`。warning 均为既有
`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
Green/Forkwell 分页、缓存、历史状态回归包含在全量验证中。

### Findy Stage B（离线实现）

根据 supervisor 提供的 Stage A.1 证据，独立 adapter 使用
`https://findy-code.io/recommends` 与 `?page=N`，固定 source 为
`おすすめ求人`。严格 detail path 为
`/companies/<numeric>/jobs/<opaque>`（opaque 仅 ASCII 字母、数字、下划线、连字符）；
拒绝 query、fragment、编码斜杠、额外路径与 foreign origin。
组合身份 `findy:<company_id>:<job_key>`、来源游标 `findy:おすすめ求人`
沿用现有缓存与人工状态机制，POLICY_VERSION 不变。
列表和详情仅 goto/evaluate/wait；首次读取后最多同页重试 4×500ms，
异常结构、登录与身份漂移立即 fail-closed。
详情只读取 visible h1 和固定 heading/carrier 的后续局部 sibling 内容，
在固定字段或 heading/section 边界停止，不读 full body，不用 site CSS selector。

新增 `tests/test_findy_search.py` 与 SearchConfig Findy restore 回归；
覆盖组合身份、strict URL、source page drift、重试、安全 canonical、
语义边界、cache/cursor namespace、CLI/WebUI、badge/external URL 与固定遥测。
测试数据全部虚构，模型 mocked；真实浏览器/数据库/招聘网站/付费模型访问 none。
当前工具环境无 subagent，主 Agent 自行验证。

全量使用仓库已有的测试进程 bounded_select workaround，精确命令：

```bash
TMPDIR="$PWD/.test-tmp" /home/zmang/scoutfilter/scout-agent/.venv/bin/python - <<'PY'
import selectors
import pytest
original = selectors.EpollSelector.select
def bounded_select(self, timeout=None):
    return original(self, 0.05 if timeout is None else min(timeout, 0.05))
selectors.EpollSelector.select = bounded_select
raise SystemExit(pytest.main(['-q', '--basetemp=.test-tmp/full']))
PY
```

退出码 0：`1006 passed, 1 warning in 39.86s`；
failed/skipped/xfailed none。唯一 warning 是既有
`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
普通 pytest 在 sandbox TestClient 跨线程等待处未完成，无最终摘要；
上述结果不代表普通 pytest 在此 sandbox 中通过，不更改生产事件循环。

CLI smoke：`/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m scout_agent search findy --help`，
退出码 0；stdout 含 `{green,forkwell,lapras,findy}`、source `おすすめ求人`、
`--coverage-pages` 与 `--max-depth`，没有启动真实 Search。
`git diff --check` 与 `git diff --cached --check` 均退出码 0，无输出。

CLI live / WebUI live 未运行；候选池真实 badge/link 验收仍待授权环境。
等待 supervisor final APPROVED，由 bridge 处理同一 PR #18；本任务不提交、push 或 merge。
