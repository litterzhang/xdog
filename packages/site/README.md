# xdog-site

Linux / Python 3.12+. A Flask documentation and demonstration **application** for
the eight public XDOG packages. It is maintained in this repository but is not
part of the eight-package PyPI publishing matrix.

## Run from the workspace

```bash
uv sync --locked
export XDOG_SITE_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
uv run xdog-site
```

Open `http://127.0.0.1:8080`. This command starts the development server; it is not
an argument-parsing management CLI. For a production WSGI host the factory is
`xdog.site.app:create_app`. See [deployment](deploy/README.md).

| Variable | Default | Purpose |
|---|---|---|
| `XDOG_SITE_HOST` | `127.0.0.1` | Development server bind address |
| `XDOG_SITE_PORT` | `8080` | Development server port |
| `XDOG_SITE_SECRET` | insecure development placeholder | Set a strong external value for deployment |
| `XDOG_SITE_DEV` | unset | Reload templates and disable static caching, without Flask debug |
| `XDOG_SITE_DEBUG` | unset | Enable debug; local development only |
| `XDOG_SITE_BASE_URL` | `https://xdog.942295.xyz` | Canonical sitemap/robots origin |

## Content and routes

- `/`: package layers and installation entry points.
- `/packages/all`: the complete package catalog.
- `/packages/<name>`: package overview; `/design`, `/features`, `/reference` and
  `/roadmap` provide deeper documentation. Flow also has `/examples`.
- `/blog`, `/faq`, `/robots.txt`, `/sitemap.xml`: articles, current answers and
  registry-derived discovery links.
- `/havefun/flow`: trusted/local workflow viewer and execution demonstration.

`content/packages.py` is the eight-package catalog shared by home, navigation and routes.
Site itself has no catalog entry or package pages; its documentation stays in this
README and the deployment guide.
Static pages live in `content/pages/<name>/*.md`; dynamic Features/Roadmap content
uses `PackageDocs`. Blog posts are dated historical articles, not current API
references. Content is authored against source, without importing the described
business runtimes just to render documentation.

## HaveFun is trusted execution, not a sandbox

The static site depends on Flask, Markdown and rendering helpers. The HaveFun
integration lazily imports Flow/Agent/AI and reads shipped workflow examples;
run it from the workspace/umbrella installation. Site-only dependencies do not
make it a standalone workflow execution distribution.

Uploaded workflows may import/run Python, invoke tools and make authenticated
model calls with host authority. A strong Flask secret **does not sandbox or
authorize these operations**. Restrict access to trusted/local users or disable
access to the runner endpoints at deployment; do not expose arbitrary workflow
submission as a safe public service. No new authentication or isolation layer is
implied by these docs.

## Verify

```bash
uv run pytest packages/site/tests -q
uv run ruff check packages/site
uv run mypy --strict -p xdog.site
uv build --package xdog-site --out-dir /tmp/xdog-site-dist
```

Route tests cover every catalog entry, navigation, sitemap, packaged Markdown,
current capability boundaries and provider-independent offline rendering. Demo
execution tests use local/stubbed integration, not live paid providers.

Source is AGPL-3.0-or-later. See [LICENSE](LICENSE) and the
[Generated Output Exception](LICENSE-EXCEPTION.md); it is not an MIT grant for
the site or the libraries.

## Native reading API

`GET /api/public/v1/home`, `/packages`, `/packages/{name}/{section}` and `/faq`
export the existing first-party content without authentication or runtime imports.
Sections are overview/features/design/reference/roadmap; Markdown remains source
Markdown and dynamic sections retain their structured metadata. Responses provide
schema version, canonical path, content revision and ETag/conditional GET.
`updated_at` is null unless the source explicitly authors it; deployment mtime is
not a publication date. Chinese mobile summaries and the HTML homepage share the
same quickstart command constants. This API exposes no Blog, HaveFun or execution
endpoints. Future Blog integration will use an articles-service channel, not a
second article feed maintained by this API.
