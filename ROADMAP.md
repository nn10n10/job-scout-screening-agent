# Roadmap

Implemented: Green, type, doda corporate offers, and マイナビ転職 corporate Scouts over CDP, SQLite deduplication, Mock/Codex/Gemini classification, resumable Codex batch evaluation, and HTML/JSON reports. type, doda, and マイナビ use bounded recent-job scans and per-run audit records. doda and マイナビ use post-detail 0-token local role filtering (`DETAIL_LOCAL_SKIP`). マイナビ's list has Scout subjects but no separate job titles, so its title stage conservatively opens eligible details rather than treating marketing copy as a job title. doda partner-agent and マイナビ agent-Scout lists are not implemented without sufficient authenticated DOM evidence.

Next platform adapter priority:

1. type — implemented
2. doda — corporate offers implemented; partner-agent list pending live DOM evidence
3. マイナビ転職 — corporate Scout list implemented; agent Scout list pending
4. Forkwell
5. LAPRAS

Each remaining platform must be inspected in an already logged-in browser before implementing selectors. Keep all browsing and scanning read-only.
