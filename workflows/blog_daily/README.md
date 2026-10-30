# Daily blog summary

Saved xdog-flow workflow for https://blog.942295.xyz:

```text
sitemap + article crawl → SDK agent summary → dated Markdown + fingerprints
```

- **Schedule:** daily at 09:00 in the host timezone (currently Asia/Shanghai).
- **Agent:** in-process xdog.agent SDK, Copilot provider, `gpt-5-mini`.
  No external coding-agent CLI. Uses the user's existing Copilot authentication.
- **Output:** `~/.local/share/xdog-flow/daily-summaries/blog-942295/YYYY-MM-DD.md`
  and `latest.md`. The JSON sidecar stores crawl evidence; `seen.json` tracks
  successfully summarized content hashes.
- **First run:** an explicitly labeled archive baseline. Subsequent runs summarize
  new/changed articles since the last successful publication, not necessarily
  articles published that day. Days without changes still get a daily report.
- **Crawler:** respects robots.txt, discovers dated article URLs from sitemap.xml,
  strips navigation/scripts, hashes full article text to detect edits even when
  sitemap lastmod is stale, and sends up to 6,000 characters per changed article
  to the agent. Checks at most 100 posts per run. Reports partial failures and
  truncation; all-article failure fails the run without advancing fingerprints.
- **Reruns:** a same-day no-change run preserves that day's existing summary.
  A same-day run with changes replaces the dated summary with the new digest.
- **Persistence:** systemd user timer is enabled and `Persistent=true` catches up
  missed firings. This host already has user lingering enabled. Execution needs
  the machine online and valid Copilot authentication.

## Run manually

From the repository root:

```bash
xdog-flow validate workflows/blog_daily/workflow.json
xdog-flow run workflows/blog_daily/workflow.json --timeout 300 \
  --allow-path "$HOME/.local/share/xdog-flow/daily-summaries/blog-942295"
```

## Scheduling

The schedule is installed as `blog-942295-daily.timer` and `.service`. It uses
this repository's existing `.venv` interpreter; keep the repository and virtualenv
at their current paths. Sibling Python modules are copied into the installed
bundle. Reinstall after editing the workflow or crawler:

```bash
xdog-flow scheduling install workflows/blog_daily/workflow.json \
  --no-venv --python "$PWD/.venv/bin/python" \
  --allow-path "$HOME/.local/share/xdog-flow/daily-summaries/blog-942295"

systemctl --user list-timers blog-942295-daily.timer
journalctl --user -u blog-942295-daily.service
systemctl --user start blog-942295-daily.service  # run installed bundle now
xdog-flow scheduling uninstall blog-942295-daily
```

The `--allow-path` grant explicitly permits publication outside the workflow's
runtime directory. Provider credentials are not stored in the workflow or units.
Change `schedule.cron` to adjust the time and reinstall. The run has a 20-minute
systemd timeout.

## Offline tests

```bash
pytest -q workflows/blog_daily/test_blog_daily_nodes.py
xdog-flow test workflows/blog_daily/workflow.json --allow-script-stub
```

The workflow test stubs network/publication boundaries and the agent to test
port wiring without side effects. The seven Python tests run crawler and
publication logic against mocked network responses and temporary directories.
