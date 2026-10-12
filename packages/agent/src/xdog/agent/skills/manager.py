"""Skill manager — load, save, list, patch, remove skills.

Skills come from three tiers, each overriding the one before it:

- **Packaged** — ``<installed package>/skills/<slug>/`` — discovered, read-only
- **Shared** — ``{data_dir}/skills/`` — available to all groups
- **Group** — ``{workspace}/skills/`` — specific to one group

Packaged skills arrive with the distributions the user installed, so
``pip install xdog-flow`` is all it takes for an agent to know how to write a
flow workflow. They are read-only: they live in site-packages, and a user who
wants to change one shadows it by saving a skill with the same slug, which
lands in the shared tier and wins.

New skills are saved to the shared directory by default (so other groups
benefit). Use ``scope="group"`` to save a group-specific skill.
"""
from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import yaml
from xdog.agent.skills.discovery import packaged_skills
from xdog.agent.skills.types import Skill

logger = logging.getLogger(__name__)


#: Fields the open Agent Skills standard defines. Its reference validator
#: rejects a file carrying anything else, so our own bookkeeping has to live
#: inside `metadata`, which the standard reserves for exactly that.
SPEC_FIELDS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")

#: `name`: 1–64 chars of [a-z0-9-], no leading/trailing hyphen, no doubled hyphen.
_NAME_RE = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)$")


def is_valid_skill_name(name: str) -> bool:
    """Whether a name satisfies the standard, and so is portable to other clients."""
    return bool(_NAME_RE.fullmatch(name))


def _slugify(name: str) -> str:
    """Convert a name to a slug the standard will accept.

    Not merely filesystem-safe: the standard constrains `name` to 1–64
    characters of ``[a-z0-9-]`` with no leading, trailing or doubled hyphen,
    and its validator rejects the file otherwise. Lowercasing and swapping
    spaces for hyphens satisfies the common case and quietly produces
    ``-weird-``, ``a--b`` or a ID-character name for the rest.
    """
    slug = re.sub(r"[^a-zA-Z0-9\s-]", "", name.lower())
    slug = re.sub(r"[\s]+", "-", slug.strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")[:64].rstrip("-")
    return slug or "untitled"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter from the markdown body.

    The frontmatter really is YAML — the format is shared with other agent
    tooling — so it is parsed with a YAML parser rather than approximated.
    Splitting on ``:`` and taking the rest of the line gets 57 of the 58 real
    SKILL.md files on a developer's machine right, and the one it fails is
    instructive: a quoted value comes back still wearing its quotes, and the
    agent is told its own description is `"You MUST..."` including the marks.
    Quoting is not exotic — YAML requires it whenever a value would otherwise
    be ambiguous — so that failure is waiting in any corpus large enough.

    Values are flattened to strings because that is what :class:`Skill` holds;
    a field written as a list still round-trips to something readable rather
    than being dropped.
    """
    if not text.startswith("---"):
        return {}, text

    # Match a closing delimiter only when it is a line of its own. `find("---")`
    # would also match a `---` inside a value or an em-dash rule in the body.
    match = re.search(r"^---\s*$", text[3:], re.MULTILINE)
    if match is None:
        return {}, text
    fm_text = text[3:3 + match.start()]
    body = text[3 + match.end():].strip()

    try:
        loaded = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        # A malformed header should cost the skill its metadata, not raise in
        # the middle of listing a directory the user did not write.
        logger.warning("could not parse skill frontmatter: %s", exc)
        return {}, body

    if not isinstance(loaded, dict):
        return {}, body

    meta: dict[str, str] = {}
    nested: dict[str, str] = {}
    for key, value in loaded.items():
        if value is None:
            continue
        if isinstance(value, dict):
            # `metadata` is the standard's slot for application fields. Lift its
            # string entries so callers read `created` the same way whether it
            # was written there or, by an older version of this code, at the top
            # level. Collected separately so it can never shadow a real field.
            for sub_key, sub_value in value.items():
                # Any scalar, not just str/int/float/bool: YAML resolves an
                # unquoted `created: 2026-01-01` to a `datetime.date`, and a
                # hand-written date is exactly what this field attracts. The
                # narrower check dropped it silently.
                if not isinstance(sub_value, (dict, list, tuple, set)) and sub_value is not None:
                    nested[str(sub_key)] = str(sub_value).strip()
            continue
        if isinstance(value, str):
            meta[str(key)] = value.strip()
        elif isinstance(value, (list, tuple)):
            meta[str(key)] = ", ".join(str(v) for v in value)
        else:
            meta[str(key)] = str(value)

    for key, value in nested.items():
        meta.setdefault(key, value)

    return meta, body


def _build_frontmatter(name: str, description: str, created: str, updated: str) -> str:
    """Build the YAML frontmatter block.

    Dumped rather than formatted, because an agent writes its own skills and
    will eventually describe one as ``Fix bug: retry on 500``. Pasted into
    ``description: {}`` that is a YAML syntax error, and the skill comes back
    with no metadata at all — it round-trips only as long as nobody writes a
    colon. The dumper quotes when it has to and leaves prose alone when it
    doesn't.

    ``created`` and ``updated`` are ours, not the standard's, so they go under
    ``metadata`` — the field the standard reserves for exactly this. Written at
    the top level they are unknown keys, and the reference validator rejects
    the whole file over them, which would make every skill an agent writes here
    unusable in any other client.
    """
    fields: dict[str, object] = {"name": name}
    if description:
        fields["description"] = description
    fields["metadata"] = {"created": created, "updated": updated}
    # sort_keys=False to keep name first, where a human skimming expects it.
    # allow_unicode so a Chinese description is not mangled into escapes.
    dumped = yaml.safe_dump(
        fields,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,  # one field per line; wrapped values are harder to skim
    ).strip()
    return f"---\n{dumped}\n---"


def _load_skill_from_dir(skill_dir: Path, *, slug: str = "", packaged: bool = False) -> Skill | None:
    """Load a skill from a directory."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None

    text = skill_file.read_text(encoding="utf-8")
    return _skill_from_text(skill_dir, text, slug=slug, packaged=packaged)


def _skill_from_text(skill_dir: Path, text: str, *, slug: str = "", packaged: bool = False) -> Skill:
    """Shared decoding after a caller has read/validated its chosen file."""
    skill_file = skill_dir / "SKILL.md"
    meta, body = _parse_frontmatter(text)

    desc = meta.get("description", "")
    if not desc:
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                desc = line[:120]
                break

    return Skill(
        name=meta.get("name", skill_dir.name),
        slug=slug or skill_dir.name,
        description=desc,
        content=body,
        created=meta.get("created", ""),
        updated=meta.get("updated", ""),
        path=skill_file,
        packaged=packaged,
        scope="turn" if meta.get("scope", "").strip().lower() == "turn" else "session",
    )


class SkillManager:
    """Manages reusable skills across three tiers, later overriding earlier.

    - ``packaged``: shipped inside installed packages, read-only
    - ``shared_dir``: skills available to all groups
    - ``group_dir``: skills specific to one group
    """

    def __init__(
        self,
        shared_dir: Path,
        group_dir: Path | None = None,
        packaged: Mapping[str, Path] | None = None,
    ) -> None:
        # Directories are created when a skill is saved, not when one is read.
        # Constructing a manager to *look at* skills used to create the
        # directory it looked in — which meant running a flow workflow left an
        # empty `skills/` beside it, and reading was not read-only.
        self._shared_dir = shared_dir
        self._group_dir = group_dir
        # None means "discover what is installed"; pass {} to opt out, which is
        # what tests want so an installed package cannot change their fixtures.
        self._packaged = dict(packaged_skills() if packaged is None else packaged)

    def list_skills(self) -> list[Skill]:
        """List all skills. Later tiers override earlier ones on slug conflict."""
        by_slug: dict[str, Skill] = {}

        for slug, skill_dir in self._packaged.items():
            skill = _load_skill_from_dir(skill_dir, slug=slug, packaged=True)
            if skill:
                by_slug[slug] = _without_body(skill)

        for skill in self._list_from_dir(self._shared_dir):
            by_slug[skill.slug] = skill

        # Group overrides shared
        if self._group_dir:
            for skill in self._list_from_dir(self._group_dir):
                by_slug[skill.slug] = skill

        return sorted(by_slug.values(), key=lambda s: s.slug)

    def load_skill(self, slug: str) -> Skill | None:
        """Load full skill content, most specific tier first."""
        if self._group_dir:
            skill = _load_skill_from_dir(self._group_dir / slug)
            if skill:
                return skill
        shared = _load_skill_from_dir(self._shared_dir / slug)
        if shared:
            return shared
        packaged_dir = self._packaged.get(slug)
        if packaged_dir:
            return _load_skill_from_dir(packaged_dir, slug=slug, packaged=True)
        return None

    def save_skill(
        self,
        slug: str,
        content: str,
        *,
        name: str = "",
        description: str = "",
        scope: str = "shared",
    ) -> Skill:
        """Create or overwrite a skill.

        ``scope`` is ``"shared"`` (default, all groups) or ``"group"``
        (this group only).
        """
        if not slug:
            slug = _slugify(name or "untitled")

        if scope == "group" and self._group_dir:
            target_dir = self._group_dir
        else:
            target_dir = self._shared_dir

        skill_dir = target_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"

        today = date.today().isoformat()

        # Preserve created date if updating
        created = today
        if skill_file.exists():
            old_text = skill_file.read_text(encoding="utf-8")
            old_meta, _ = _parse_frontmatter(old_text)
            created = old_meta.get("created", today)

        fm = _build_frontmatter(
            name=name or slug,
            description=description,
            created=created,
            updated=today,
        )

        full_text = f"{fm}\n\n{content}\n"
        skill_file.write_text(full_text, encoding="utf-8")

        logger.info("Saved skill: %s (%s, scope=%s)", name or slug, skill_file, scope)

        return Skill(
            name=name or slug,
            slug=slug,
            description=description,
            content=content,
            created=created,
            updated=today,
            path=skill_file,
        )

    def patch_skill(self, slug: str, patch_text: str) -> Skill | None:
        """Append content to an existing skill (token-efficient)."""
        skill = self.load_skill(slug)
        if skill is None:
            return None

        return self.save_skill(
            slug,
            skill.content + "\n\n" + patch_text,
            name=skill.name,
            description=skill.description,
        )

    def remove_skill(self, slug: str) -> bool:
        """Delete a skill from the writable tiers.

        A packaged skill is never touched: its directory is inside
        site-packages, and ``rmtree`` there would quietly damage an installed
        distribution to satisfy what is only a request to hide a skill. Users
        who want one gone should uninstall the package that ships it.
        """
        removed = False
        for d in [self._group_dir, self._shared_dir]:
            if d and (d / slug).exists():
                shutil.rmtree(d / slug)
                removed = True
                logger.info("Removed skill: %s from %s", slug, d)
        if not removed and slug in self._packaged:
            logger.info(
                "Skill %s is shipped by an installed package and cannot be removed", slug
            )
        return removed

    def skills_summary(self) -> str:
        """One-line descriptions for progressive disclosure in the prompt."""
        skills = self.list_skills()
        if not skills:
            return ""
        lines = ["# Available Skills", ""]
        for s in skills:
            desc = f" — {s.description}" if s.description else ""
            lines.append(f"- `{s.slug}`{desc}")
        lines.append("")
        lines.append(
            "Full skill bodies are not included in this index. Use only the host's "
            "available controls or explicitly provided tools to load them."
        )
        return "\n".join(lines)

    def _list_from_dir(self, skills_dir: Path) -> list[Skill]:
        """List skills from a single directory (metadata only)."""
        skills: list[Skill] = []
        if not skills_dir.exists():
            return skills

        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill = _load_skill_from_dir(skill_dir)
            if skill:
                skills.append(_without_body(skill))

        return skills


def _without_body(skill: Skill) -> Skill:
    """Drop the body for listing — progressive disclosure.

    Every skill on disk contributes its description to the prompt, so bodies
    must not travel with them: a dozen skills would otherwise cost a dozen full
    documents before the agent has decided it needs any of them.
    """
    return Skill(
        name=skill.name,
        slug=skill.slug,
        description=skill.description,
        created=skill.created,
        updated=skill.updated,
        path=skill.path,
        packaged=skill.packaged,
        scope=skill.scope,
    )
