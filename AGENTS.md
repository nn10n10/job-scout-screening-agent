# Long-term safety constraints

- This is a read-only job Scout screening tool. Browser automation may navigate and read pages, but must never apply, reply, decline, express interest, favorite, change account settings, upload files, or deliberately mark messages read.
- Use the user's already authenticated Chrome over CDP. Never automate Google login, collect passwords or 2FA, spoof browser identity, or bypass OAuth/security checks. Keep persistent Chromium only as an explicit legacy mode; never silently fall back to it when CDP fails.
- Never commit `.env`, credentials, tokens, cookies, Chrome profiles, `auth.json`, real SQLite databases, generated reports, real Scout messages/JDs, or files from `~/.codex`. Fixtures must be fictional and de-identified.
- Keep Codex execution non-interactive, isolated in a temporary directory, and read-only. Do not access browsers or recruitment sites from the classifier. Tests must mock Codex/Gemini calls and must not consume real model quota.
- Treat existing real evaluations as user data. Do not overwrite them during development or tests; in particular preserve the already completed Codex evaluations. Prefer `--replace-provider` and `--dry-run` over broad `--force`.
- KEEP requires evidence that Cloud/Infrastructure/Platform/DevOps/SRE is one of the job's main responsibilities. Keywords such as AWS/Terraform/CI/CD alone are insufficient. Missing evidence should remain unknown or MAYBE, not be invented.
- Explanatory evaluation fields (`summary`, `reasons`, `concerns`) use Simplified Chinese, preserving company names, job titles, and technical terms in their original language.

Next platform priority: 1. type; 2. doda; 3. マイナビ転職; 4. Forkwell; 5. LAPRAS. No new platform adapter is part of this checkpoint.
