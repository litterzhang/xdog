"""Finite read-only content documents: never import or execute business runtimes."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from xdog.site.content.docpages import STATIC_PAGES, read_page
from xdog.site.content.faq import FAQS
from xdog.site.content.packages import PACKAGES, PACKAGES_BY_NAME, Package
from xdog.site.content.product import QUICKSTART_MARKDOWN, SUMMARIES_ZH
from xdog.site.content.registry import PACKAGE_DOCS

PUBLIC_SECTIONS = ('overview', 'features', 'design', 'reference', 'roadmap')


def document(identifier: str, title: str, kind: str, path: str, **content: Any) -> dict[str, Any]:
    value = {'schema_version': 1, 'id': identifier, 'title': title,
             'content_type': kind, 'canonical_path': path, 'updated_at': None, **content}
    value['source_revision'] = hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return value


def package_summary(package: Package) -> dict[str, Any]:
    sections = [section for section in STATIC_PAGES if read_page(package.name, section) is not None]
    return {'name': package.name, 'title': package.name.capitalize(), 'dist': package.dist,
            'tagline': package.tagline, 'summary': list(package.summary),
            'summary_zh': SUMMARIES_ZH.get(package.name, package.tagline),
            'install': package.install, 'github_url': package.readme_url, 'pypi_url': package.pypi_url,
            'sections': [section for section in PUBLIC_SECTIONS
                         if section in sections or section in ('features', 'roadmap')]}


def home() -> dict[str, Any]:
    return document('home', 'XDOG', 'home', '/',
                    tagline='把一次有用的提示，变成可以反复运行的工作。',
                    description='从代码仓库中的助手开始，逐步把有效步骤组织成流程。工具、权限和状态由你自己的宿主掌控。',
                    requirements='Linux · Python 3.12+', featured=['coding', 'flow', 'claw'],
                    packages=[package_summary(PACKAGES_BY_NAME[name]) for name in ('coding', 'flow', 'claw')],
                    quickstart=document('quickstart', '快速开始', 'markdown', '/#get-started',
                                        content=QUICKSTART_MARKDOWN))


def catalog() -> dict[str, Any]:
    return document('packages', '全部组件', 'catalog', '/packages/all',
                    items=[package_summary(package) for package in PACKAGES])


def package_document(name: str, section: str) -> dict[str, Any] | None:
    if name not in PACKAGES_BY_NAME or section not in PUBLIC_SECTIONS:
        return None
    path = f'/packages/{name}/{section}'
    if section in ('features', 'roadmap'):
        docs = PACKAGE_DOCS[name]
        entries = docs.features if section == 'features' else docs.roadmap
        intro = docs.features_intro if section == 'features' else docs.roadmap_intro
        return document(f'packages/{name}/{section}', f'{name.capitalize()} · {section.capitalize()}',
                        section, path, introduction=intro, items=[asdict(entry) for entry in entries],
                        package=package_summary(PACKAGES_BY_NAME[name]))
    source = read_page(name, section)
    if source is None:
        return None
    return document(f'packages/{name}/{section}', source.title, 'markdown', path,
                    content=source.content, updated_at=source.updated_at,
                    package=package_summary(PACKAGES_BY_NAME[name]))


def faq() -> dict[str, Any]:
    return document('faq', '常见问题', 'faq', '/faq',
                    items=[{'id': hashlib.sha256(row['q'].encode()).hexdigest()[:12],
                            'question': row['q'], 'answer': row['a']} for row in FAQS])
