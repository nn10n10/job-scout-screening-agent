# TASK-016 隔离验证记录

主 Agent 实施并执行验证；当前工具没有可调用的验证子代理。只使用虚构 JD、mock 模型、worktree 内临时 SQLite 和 Node 虚构 DOM。真实浏览器、用户数据库、招聘网站、网络、付费模型访问：none。真实 CLI 搜索/真人 Chrome probe：not run；没有提交、推送、修改分支/git metadata、发布 GitHub 消息或创建 PR。

## 验证命令

全量命令（selector 等待上限只在测试进程中生效；不修改应用或安装依赖）：

```bash
/home/zmang/scoutfilter/scout-agent/.venv/bin/python - <<'PY'
import selectors
import pytest
original = selectors.EpollSelector.select
def bounded_select(self, timeout=None):
    return original(self, 0.02 if timeout is None else min(timeout, 0.02))
selectors.EpollSelector.select = bounded_select
raise SystemExit(pytest.main(['-q', '--tb=short', '--basetemp=.test-tmp/task016-final']))
PY
```

普通专项命令 `/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m pytest -q tests/test_development_sources.py tests/test_indeed_search.py tests/test_cloud_app_policy.py` 在既有 TestClient 跨线程等待处停滞，已中断（退出码 130，无最终 pytest 摘要）。仓库 TASK-015 已记录相同 sandbox 限制；因此使用上述 selector 上限方式，不跳过测试、不缩减 ASGI 断言。专项用同样包装、`--basetemp=.test-tmp/task016` 执行时得到 `217 passed, 1 warning in 1.72s`（退出码 0）。

第一次全量：`1 failed, 1908 passed, 1 warning in 54.53s`（退出码 1）。失败为旧 `test_paging_fields_visibility_switches_with_platform` 的独立 JS 夹具缺少新增 `sourcePresets` 全局上下文，报 `ReferenceError`；补充空预设上下文，保持原分页断言。

最终全量：`1909 passed, 1 warning in 54.17s`，退出码 0；failed/skipped/xfailed：none。唯一警告是现有 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`，未调整依赖。

其他命令：

- `/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m scout_agent search --help`：退出码 0；关键 stdout 包含三个无 AWS 限定的完整关键词、八个平台及 `--source-preset {development,infra,all}`，帮助说明「白名单 source label」「Indeed 新词待独立 live probe」。未运行真实搜索。
- `/home/zmang/scoutfilter/scout-agent/.venv/bin/python -m compileall -q scout_agent`：退出码 0，stdout none。
- `git diff --check`：退出码 0，stdout none。
- `git diff --no-index --check /dev/null tests/test_development_sources.py`：退出码 1（新增文件存在差异的正常 no-index 返回值），空白错误输出 none。

## 覆盖与边界

直接新增 `tests/test_development_sources.py`：三个新增 q 的 URL shape、虚构列表与 jk/canonical 身份、page1-only、challenge/offset fail-closed；CLI presets 与逐项 keyword；后端八平台独立白名单；模板实际预设点击、切换平台、sessionStorage 选择保存/恢复；后台 runner 的开发-only argv 无 Infra；混合 max_jobs/max_model_jobs/early_stop=1、含已有缓存职位、固定页与默认增量模式，保留 EXCLUDED 与缓存评价。原 `tests/test_web_search_config.py` 保留分页字段切换断言，仅同步夹具上下文。

全量包括 `tests/test_cloud_app_policy.py` 的云开发职责证据/仅技术栈未知/普通非云开发拒绝、年收与 SES 硬排除、日语否定句/公司 SES 背景区分、配属未知、历史缓存与人工状态；以及八平台 adapter/CLI/WebUI/source telemetry/分页/身份/安全回归。

Indeed 共十个来源，新增通用 Backend / Full-stack / Server-side，保留原七个。开发-only 只访问开发六来源；混合与开发组都优先通用三职种，预算共享，不承诺极小预算覆盖每一来源。固定页模式仍可能因已有缓存结果达到 early_stop；默认增量模式跳过已缓存结果继续尝试其他开发来源。没有修改筛选语义或 POLICY_VERSION，没有历史批量重算。

新增 Indeed q **尚未 live verified**；Green/Type/doda/Mynavi 的新开发职种 route 均 `PENDING_LIVE_SOURCE_EVIDENCE`，未猜 URL/DOM/分页。Forkwell/LAPRAS/Findy 保留既有通用或推荐列表，不宣称新增关键词搜索。后续由 Bridge 独立验证、提交和建 PR，等待 supervisor Review/APPROVED；新增来源的 live probe 只能在另行授权 Chrome 环境完成。
