# Roadmap

Implemented: Green, type, and doda corporate-offer read-only Scout scanning over CDP, SQLite deduplication, Mock/Codex/Gemini classification, resumable Codex batch evaluation, and HTML/JSON reports. type and doda scan up to 100 visible jobs within a 14-day age window, with per-job title prefiltering and per-run audit records. doda also has a post-detail 0-token local role filter (`DETAIL_LOCAL_SKIP`) before hard rules and classification, with separate audit reasons. doda partner-agent Scouts are not yet implemented because the authenticated list had no observable rows.

Next platform adapter priority:

1. type — implemented
2. doda — corporate offers implemented; partner-agent list pending live DOM evidence
3. マイナビ転職
4. Forkwell
5. LAPRAS

Each remaining platform must be inspected in an already logged-in browser before implementing selectors. Keep all browsing and scanning read-only.
