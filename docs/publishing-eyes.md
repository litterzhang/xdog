# Publish xdog-eyes through GitHub Trusted Publishing

Eyes uses the existing `.github/workflows/publish.yml`; do not add another
publishing workflow or change its filename without updating PyPI authorization.
The shared matrix builds and publishes Eyes independently of the other packages.
Normal main pushes run CI only. A `v*` tag publishes all eight library/client
packages; manual dispatch can select only Eyes.

## Required authorization

The GitHub environment `pypi-xdog-eyes` exists. In the intended PyPI account,
register a pending GitHub publisher for a new project, or use the existing
project's Publishing settings if it has already been created:

| Field | Exact value |
| --- | --- |
| PyPI project | `xdog-eyes` |
| GitHub owner | `litterzhang` |
| Repository | `xdog` |
| Workflow filename | `publish.yml` |
| Environment | `pypi-xdog-eyes` |

Use the workflow filename, not its display name or full path. The publisher uses
job-scoped `id-token: write`; no API token or password is needed. PyPI account
configuration is separate from repository changes and cannot be inferred from
successful wheel builds or from the GitHub environment's existence.

The October 20, 2026 `v2.4.3` run built and tested Eyes successfully, but PyPI
rejected its OIDC exchange with `invalid-publisher` (no matching publisher).
The other seven uploads succeeded. This is an authorization issue, not a missing
workflow or an Eyes runtime failure. At this check, the public PyPI project API
returned HTTP 404 for `xdog-eyes`.

## Release checks

The publisher runs workspace tests/lint and strict Flow/Skills/Eyes type checks.
Before upload it verifies the Eyes wheel's distribution/version, tag match,
packaged `eyes` skill, both examples, scheduler modules and typing marker. Its
only approved dependencies are the bounded ACP SDK and croniter requirements;
any accidental dependency on a harness blocks publication.

The workspace shares one version; release 2.4.4 aligns all package metadata,
internal dependency floors, runtime protocol identities and the lockfile.

## Retry only Eyes after configuring PyPI

If the first Eyes upload fails authorization, configure its publisher, then run:

```bash
gh workflow run publish.yml --ref v2.4.4 -f 'packages=["xdog-eyes"]'
```

This reuses the exact tagged source without uploading already published siblings.
Do not move the tag, reuse published versions with different artifacts, or rerun
all uploads blindly. A successful dispatch does not prove the upload succeeded;
check the resulting Actions run and the PyPI project version.
