# Publish xdog-skills through GitHub Trusted Publishing

The existing `.github/workflows/publish.yml` now includes xdog-skills in the same
build/publish matrix as the other XDOG libraries. Do not create a second publishing
workflow: PyPI must trust the actual filename and environment used by this one.

## 1. GitHub environment

In `litterzhang/xdog`, open **Settings → Environments → New environment** and create:

```text
pypi-xdog-skills
```

Keep this spelling exact. For manual publishing from main, allow main in the
environment deployment policy. For the tag-triggered release workflow, also
allow `v*` tags; a main-only policy rejects tag deployments. Configure approval
reviewers if desired. This repo's publish
job already uses `environment: pypi-${{ matrix.package }}` and job-scoped
`id-token: write`. It consumes built artifacts in a separate upload job; no PyPI
API token, password or new repository secret is needed for Trusted Publishing.

## 2. Register the PyPI pending publisher

Sign into the intended PyPI owner account. Open **Account settings → Publishing**,
then the **Add a new pending publisher / GitHub** form. Use:

| Field | Value |
|---|---|
| PyPI project name | `xdog-skills` |
| GitHub owner | `litterzhang` |
| Repository name | `xdog` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi-xdog-skills` |

Workflow filename is **not** the display name `publish` and **not** the full
`.github/workflows/publish.yml` path. GitHub owner is the repository owner, not a
separate PyPI login field. Registering a pending publisher does not upload files;
its first successful authorized upload creates the new project and associates
the publisher with it. If the project already exists under your ownership, use
that project's **Manage → Publishing** instead of a pending-new-project form.

Do not reuse another package's environment: the per-package environments distinguish
first uploads through the shared matrix workflow. If PyPI reports an active-pending
limit, complete or remove unused pending entries before trying again.

## 3. First upload: select only this new package

After the pending publisher and GitHub environment are ready:

1. Open repository **Actions → publish → Run workflow**.
2. Select branch **main**.
3. Set the `packages` input to the JSON array below, then run it:

```json
["xdog-skills"]
```

Equivalent command (execute only when ready to publish):

```bash
gh workflow run publish.yml --ref main -f 'packages=["xdog-skills"]'
```

The workflow builds xdog-skills wheel/sdist, runs workspace tests/lint and the
Flow+Skills strict type gate, then requests the short-lived OIDC upload credential.
The authorized release version is now **2.4.3**, with workspace versions and
internal minimum dependencies aligned for the new Agent/host APIs.

**A normal main push does not publish.** A `v*` tag publishes the default matrix,
so do not create a broad release tag merely to first-upload xdog-skills: already
published package versions may reject duplicate uploads. No workflow dispatch,
tag or PyPI upload was executed by the implementation task.

## Reference

- PyPI: https://docs.pypi.org/trusted-publishers/adding-a-publisher/
- PyPI troubleshooting: https://docs.pypi.org/trusted-publishers/troubleshooting/

These instructions configure authorization, not a guarantee that a remote account
has already completed pending registration, GitHub environment protection or a
successful real upload. Those external-account steps are left to the account owner.

## Implementation verification and release boundary

The new publishing-type gate was observed RED (Skills namespace missing), then
GREEN:15 workflow/real-metadata cases passed2.24s; exact configured strict
Flow+Skills command passed72 source files; Ruff and diff checks passed. The core
refactor previously passed the full nine-path7085/7 suite and isolated local-wheel
consumers. These new workflow tests are an additional focused gate, not a claim
that the full suite was rerun after this configuration-only follow-up.

Before an actual release, align versions and minimum dependencies for any newly
used Agent/Coding/Claw APIs with the versions you publish. The isolated consumers
used the matching locally rebuilt wheels, NOT independently installed existing
PyPI dependency versions. Publishing configuration and a pending publisher do
not themselves establish registry dependency compatibility; the initial workflow-setup step
neither bumped versions nor uploaded packages. A subsequent explicitly authorized
2.4.3 release performs the version alignment and tag-triggered workflow.
