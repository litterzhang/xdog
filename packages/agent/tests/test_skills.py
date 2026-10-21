"""Tests for the shared skills core.

This code moved out of ``xdog.claw`` so ``xdog.coding`` could read the same
skill directories, and it arrived with no tests at all. The contracts worth
pinning are the ones a second caller can easily break: the two-tier override,
and progressive disclosure — ``list_skills`` must NOT carry bodies, or every
skill on disk lands in the prompt.
"""
from pathlib import Path

import pytest
from xdog.agent.skills import SkillManager


def _mk(root: Path, slug: str, text: str) -> Path:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    m = SkillManager(shared_dir=tmp_path / "shared", packaged={})
    m.save_skill("deploy", "Run `make deploy`.", name="Deploy", description="Ship it")

    got = m.load_skill("deploy")
    assert got is not None
    assert (got.name, got.description) == ("Deploy", "Ship it")
    assert got.content == "Run `make deploy`."


def test_group_skill_overrides_shared_of_the_same_slug(tmp_path: Path) -> None:
    shared, group = tmp_path / "shared", tmp_path / "group"
    _mk(shared, "notes", "---\nname: shared one\n---\n\nshared body")
    _mk(group, "notes", "---\nname: group one\n---\n\ngroup body")

    m = SkillManager(shared_dir=shared, group_dir=group, packaged={})

    loaded = m.load_skill("notes")
    assert loaded is not None and loaded.content == "group body"
    # ...and the override collapses to a single entry rather than listing twice.
    assert [s.name for s in m.list_skills()] == ["group one"]


def test_listing_omits_bodies_so_the_prompt_stays_small(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "big", "---\nname: big\ndescription: d\n---\n\n" + "x" * 5000)

    m = SkillManager(shared_dir=shared, packaged={})

    listed = m.list_skills()
    assert [s.description for s in listed] == ["d"]
    assert listed[0].content == "", "list_skills must not carry the body"
    # The full body is still one explicit call away.
    loaded = m.load_skill("big")
    assert loaded is not None and len(loaded.content) == 5000


def test_description_falls_back_to_first_body_line(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "undescribed", "---\nname: u\n---\n\n# Heading\n\nThe real summary.")

    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("undescribed")
    assert skill is not None
    assert skill.description == "The real summary."


def test_updating_keeps_the_original_created_date(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "old", "---\nname: old\ncreated: 2020-01-01\nupdated: 2020-01-01\n---\n\nbody")

    m = SkillManager(shared_dir=shared, packaged={})
    updated = m.save_skill("old", "new body", name="old")

    assert updated.created == "2020-01-01"
    assert updated.updated != "2020-01-01"


def test_patch_appends_and_preserves_metadata(tmp_path: Path) -> None:
    m = SkillManager(shared_dir=tmp_path / "shared", packaged={})
    m.save_skill("s", "first", name="S", description="keep me")

    patched = m.patch_skill("s", "second")
    assert patched is not None
    assert patched.content == "first\n\nsecond"
    assert patched.description == "keep me"

    assert m.patch_skill("does-not-exist", "x") is None


def test_remove_clears_both_tiers(tmp_path: Path) -> None:
    shared, group = tmp_path / "shared", tmp_path / "group"
    _mk(shared, "dup", "---\nname: a\n---\n\na")
    _mk(group, "dup", "---\nname: b\n---\n\nb")

    m = SkillManager(shared_dir=shared, group_dir=group, packaged={})
    assert m.remove_skill("dup") is True
    assert m.load_skill("dup") is None
    assert m.remove_skill("dup") is False


def test_summary_is_empty_when_there_are_no_skills(tmp_path: Path) -> None:
    # An empty string is what the prompt builder tests for; a header with no
    # entries under it would be worse than nothing.
    assert SkillManager(shared_dir=tmp_path / "shared", packaged={}).skills_summary() == ""


def test_summary_lists_every_slug_with_its_description(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "b-skill", "---\nname: B\ndescription: does b\n---\n\nbody")
    _mk(shared, "a-skill", "---\nname: A\ndescription: does a\n---\n\nbody")

    summary = SkillManager(shared_dir=shared, packaged={}).skills_summary()

    assert "`a-skill` — does a" in summary
    assert "`b-skill` — does b" in summary
    assert "body" not in summary


def test_non_directories_and_dirs_without_skill_md_are_ignored(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(parents=True)
    (shared / "stray.md").write_text("not a skill", encoding="utf-8")
    (shared / "empty").mkdir()
    _mk(shared, "real", "---\nname: real\n---\n\nbody")

    assert [s.slug for s in SkillManager(shared_dir=shared, packaged={}).list_skills()] == ["real"]


def _flow_skill_dir() -> Path:
    """Where `xdog-flow`'s shipped skill lives, in either layout.

    Under an editable install the packaged copy does not exist — the skill is
    force-included at build time — so fall back to the source directory the
    wheel is built from. Skipping instead would make this test silently pass
    for everyone who runs it from a checkout, which is everyone. CI checks the
    built artifact separately.
    """
    from importlib.resources import files

    packaged = Path(str(files("xdog.flow"))) / "skills" / "flow"
    source = Path(__file__).resolve().parents[2] / "flow" / "skills" / "flow"
    found = packaged if (packaged / "SKILL.md").exists() else source
    assert (found / "SKILL.md").exists(), f"no SKILL.md at {packaged} or {source}"
    return found


def test_a_packaged_skill_is_listed_and_loadable(tmp_path: Path) -> None:
    """The packaging path: `pip install xdog-flow` teaches an agent flow."""
    m = SkillManager(shared_dir=tmp_path / "shared", packaged={"flow": _flow_skill_dir()})

    listed = m.list_skills()
    assert [s.slug for s in listed] == ["flow"]
    assert listed[0].packaged is True
    assert listed[0].content == "", "packaged skills must honour progressive disclosure too"

    loaded = m.load_skill("flow")
    assert loaded is not None
    assert loaded.packaged is True
    assert "workflow" in loaded.content.lower()


def test_a_user_skill_shadows_a_packaged_one_of_the_same_slug(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "flow", "---\nname: mine\ndescription: my own\n---\n\nmy body")

    m = SkillManager(shared_dir=shared, packaged={"flow": _flow_skill_dir()})

    loaded = m.load_skill("flow")
    assert loaded is not None
    assert loaded.content == "my body"
    assert loaded.packaged is False
    # Shadowed, not duplicated.
    assert [s.name for s in m.list_skills()] == ["mine"]


def test_removing_a_packaged_skill_does_not_touch_site_packages(tmp_path: Path) -> None:
    """`remove_skill` calls rmtree. Pointed at site-packages it would delete
    part of an installed distribution to satisfy a request to hide a skill."""
    flow_skill = _flow_skill_dir()
    m = SkillManager(shared_dir=tmp_path / "shared", packaged={"flow": flow_skill})

    assert m.remove_skill("flow") is False
    assert (flow_skill / "SKILL.md").exists(), "the packaged skill was deleted!"
    assert m.load_skill("flow") is not None


def test_removing_a_shadow_reveals_the_packaged_skill_again(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "flow", "---\nname: mine\n---\n\nmy body")
    m = SkillManager(shared_dir=shared, packaged={"flow": _flow_skill_dir()})

    assert m.remove_skill("flow") is True
    revealed = m.load_skill("flow")
    assert revealed is not None and revealed.packaged is True


def test_discovery_is_safe_to_call_and_finds_only_real_skills() -> None:
    """Discovery walks every installed `xdog.*` package. It must not raise —
    an agent that cannot start because some unrelated package is unreadable
    would be a bad trade for a convenience feature."""
    from xdog.agent.skills import packaged_skills

    found = packaged_skills()
    assert isinstance(found, dict)
    for slug, path in found.items():
        assert (path / "SKILL.md").is_file()
        assert slug not in {"", "skill", "skills"}, "slug must be the skill's own name"


# -- Frontmatter is YAML, and must be parsed and written as YAML --


def test_a_quoted_description_does_not_keep_its_quotes(tmp_path: Path) -> None:
    """The one real-world SKILL.md that a split-on-colon parser got wrong."""
    shared = tmp_path / "shared"
    _mk(shared, "q", '---\nname: q\ndescription: "You MUST use this before creative work"\n---\n\nbody')

    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("q")
    assert skill is not None
    assert skill.description == "You MUST use this before creative work"


def test_a_colon_in_a_description_survives_the_round_trip(tmp_path: Path) -> None:
    """An agent writes its own skills, and will eventually write a colon.

    Formatted straight into `description: {}` this is a YAML syntax error, and
    the skill reloads with no metadata at all.
    """
    m = SkillManager(shared_dir=tmp_path / "shared", packaged={})
    m.save_skill("s", "body", name="s", description="Fix bug: retry on 500")

    back = m.load_skill("s")
    assert back is not None
    assert back.description == "Fix bug: retry on 500"
    assert back.content == "body"


def test_unicode_descriptions_are_not_mangled(tmp_path: Path) -> None:
    m = SkillManager(shared_dir=tmp_path / "shared", packaged={})
    m.save_skill("cn", "正文", name="工作流", description="把流程固化成 workflow.json")

    back = m.load_skill("cn")
    assert back is not None
    assert back.name == "工作流"
    assert back.description == "把流程固化成 workflow.json"


def test_a_horizontal_rule_in_the_body_is_not_read_as_the_delimiter(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "hr", "---\nname: hr\ndescription: d\n---\n\nStep one\n\n---\n\nStep two")

    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("hr")
    assert skill is not None
    assert skill.description == "d"
    assert "Step two" in skill.content


def test_malformed_frontmatter_loses_metadata_but_not_the_skill(tmp_path: Path) -> None:
    """A directory of skills is user-authored; one bad file must not raise."""
    shared = tmp_path / "shared"
    _mk(shared, "broken", "---\nname: [unclosed\n---\n\nthe body is still here")

    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("broken")
    assert skill is not None
    assert skill.content == "the body is still here"
    assert skill.name == "broken", "falls back to the directory name"


def test_list_valued_fields_are_flattened_rather_than_dropped(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "l", "---\nname: l\ndescription:\n  - first\n  - second\n---\n\nbody")

    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("l")
    assert skill is not None
    assert skill.description == "first, second"


# -- Conformance with the open Agent Skills standard --


def test_what_we_write_carries_no_fields_outside_the_standard() -> None:
    """The reference validator rejects a file with unknown top-level keys, so
    one stray bookkeeping field makes every skill we write unportable."""
    import yaml
    from xdog.agent.skills.manager import SPEC_FIELDS, _build_frontmatter

    block = _build_frontmatter("s", "d", "2026-01-01", "2026-08-06")
    loaded = yaml.safe_load(block.strip("-\n"))

    assert set(loaded) <= set(SPEC_FIELDS), f"non-standard keys: {set(loaded) - set(SPEC_FIELDS)}"
    assert loaded["metadata"] == {"created": "2026-01-01", "updated": "2026-08-06"}


def test_dates_still_round_trip_through_metadata(tmp_path: Path) -> None:
    m = SkillManager(shared_dir=tmp_path / "shared", packaged={})
    m.save_skill("s", "body", name="s", description="d")
    first = m.load_skill("s")
    assert first is not None and first.created

    updated = m.save_skill("s", "new body", name="s")
    assert updated.created == first.created, "created survives an update"


def test_dates_written_at_the_top_level_by_older_versions_are_still_read(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "legacy", "---\nname: legacy\ncreated: 2020-01-01\n---\n\nbody")

    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("legacy")
    assert skill is not None
    assert skill.created == "2020-01-01"


def test_metadata_cannot_shadow_a_real_field(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "s", "---\nname: real\nmetadata:\n  name: impostor\n---\n\nbody")

    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("s")
    assert skill is not None
    assert skill.name == "real"


@pytest.mark.parametrize(
    "raw",
    ["My Skill", "  -weird-  ", "a--b", "x" * 80, "!!!", "-", "a b  c", "UPPER--CASE-"],
)
def test_generated_slugs_always_satisfy_the_name_rules(raw: str) -> None:
    """1–64 chars of [a-z0-9-], no leading, trailing or doubled hyphen.

    An agent naming its own skill will produce all of these; a slug that
    violates the rules is rejected by other clients, not by us, so nothing
    here would catch it.
    """
    from xdog.agent.skills.manager import _slugify, is_valid_skill_name

    slug = _slugify(raw)
    assert is_valid_skill_name(slug), f"{raw!r} produced invalid name {slug!r}"


def test_a_saved_skill_is_named_after_its_directory(tmp_path: Path) -> None:
    """The standard requires frontmatter `name` to match the parent directory."""
    m = SkillManager(shared_dir=tmp_path / "shared", packaged={})
    saved = m.save_skill("", "body", name="My Great Skill")

    assert saved.slug == "my-great-skill"
    assert (tmp_path / "shared" / "my-great-skill" / "SKILL.md").exists()


def test_a_package_can_ship_more_than_one_skill(tmp_path: Path) -> None:
    """What the `skills/<name>/` layout buys over a fixed `skill/` directory.

    The old layout could only ever carry one skill per distribution, and named
    it after the package — which the standard forbids, since `name` has to
    match the directory it sits in.
    """
    container = tmp_path / "skills"
    for slug in ("first-skill", "second-skill"):
        d = container / slug
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: {slug} does things\n---\n\n{slug} body",
            encoding="utf-8",
        )

    packaged = {p.name: p for p in sorted(container.iterdir())}
    m = SkillManager(shared_dir=tmp_path / "shared", packaged=packaged)

    assert [s.slug for s in m.list_skills()] == ["first-skill", "second-skill"]
    loaded = m.load_skill("second-skill")
    assert loaded is not None and loaded.content == "second-skill body"


def test_our_own_shipped_skill_conforms() -> None:
    """`name` must equal the directory it lives in, or other clients reject it."""
    from xdog.agent.skills.manager import SPEC_FIELDS, _parse_frontmatter, is_valid_skill_name

    skill_dir = _flow_skill_dir()
    meta, body = _parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))

    assert meta.get("name") == skill_dir.name
    assert is_valid_skill_name(skill_dir.name)
    assert 0 < len(meta.get("description", "")) <= 1024
    assert set(meta) <= set(SPEC_FIELDS) | {"created", "updated"}
    assert len(body.splitlines()) < 500, "the standard recommends under 500 lines"


# -- Declared lifetime --


def test_anything_that_is_not_turn_means_session(tmp_path: Path) -> None:
    """Absent and unrecognised both mean "stays" — the safe direction.

    Merged from two tests that asserted the same thing about different inputs.
    An unknown value must never be read as "expire": silently dropping a
    guardrail is the expensive mistake, keeping a finished skill is not.
    """
    shared = tmp_path / "shared"
    _mk(shared, "absent", "---\nname: absent\ndescription: d\n---\n\nbody")
    _mk(shared, "unknown", "---\nname: unknown\ndescription: d\nmetadata:\n  scope: forever\n---\n\nb")

    m = SkillManager(shared_dir=shared, packaged={})
    for slug in ("absent", "unknown"):
        skill = m.load_skill(slug)
        assert skill is not None
        assert skill.scope == "session" and skill.expires_after_turn is False


def test_an_author_can_declare_a_turn_scoped_skill(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _mk(shared, "once", "---\nname: once\ndescription: d\nmetadata:\n  scope: turn\n---\n\nbody")

    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("once")
    assert skill is not None
    assert skill.expires_after_turn is True


def test_scope_survives_the_listing_projection(tmp_path: Path) -> None:
    """`list_skills` strips bodies; it must not strip the lifetime with them."""
    shared = tmp_path / "shared"
    _mk(shared, "once", "---\nname: once\ndescription: d\nmetadata:\n  scope: turn\n---\n\nbody")

    listed = SkillManager(shared_dir=shared, packaged={}).list_skills()
    assert [s.expires_after_turn for s in listed] == [True]


# -- Bundled files have to be findable --


def test_the_skill_directory_is_stated_so_relative_paths_resolve(tmp_path: Path) -> None:
    from xdog.agent.skills import render_skill_body

    shared = tmp_path / "shared"
    _mk(shared, "s", "---\nname: s\ndescription: d\n---\n\nSee `examples/x.json`.")
    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("s")
    assert skill is not None

    rendered = render_skill_body(skill)

    assert str(shared / "s") in rendered
    assert "See `examples/x.json`." in rendered


@pytest.mark.parametrize("variable", ["${SKILL_DIR}", "${CLAUDE_SKILL_DIR}"])
def test_skill_dir_variables_are_substituted(tmp_path: Path, variable: str) -> None:
    """`CLAUDE_SKILL_DIR` too, so a skill written for that client works here."""
    from xdog.agent.skills import render_skill_body

    shared = tmp_path / "shared"
    _mk(shared, "s", f"---\nname: s\ndescription: d\n---\n\nRun {variable}/scripts/go.sh")
    skill = SkillManager(shared_dir=shared, packaged={}).load_skill("s")
    assert skill is not None

    rendered = render_skill_body(skill)

    assert variable not in rendered
    assert f"{shared / 's'}/scripts/go.sh" in rendered


def test_rendering_a_skill_with_no_path_changes_nothing() -> None:
    from xdog.agent.skills import Skill, render_skill_body

    detached = Skill(name="s", slug="s", content="body ${SKILL_DIR}")
    assert render_skill_body(detached) == "body ${SKILL_DIR}"


def test_every_file_our_shipped_skill_points_at_actually_exists() -> None:
    """The bug this whole module exists for.

    SKILL.md says "read the closest example and copy its shape" and ships
    twelve of them. Every one of those paths was unresolvable, because nothing
    told the model where they were — a failure that yields a confused agent
    rather than an error, which is how it survived being written and packaged.
    """
    import re

    from xdog.agent.skills.manager import _parse_frontmatter

    skill_dir = _flow_skill_dir()
    _, body = _parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))

    referenced = set(re.findall(r"`((?:examples|scripts|references|assets)/[\w./-]+)`", body))
    assert referenced, "expected the skill to point at its bundled files"

    missing = sorted(p for p in referenced if not (skill_dir / p).exists())
    assert not missing, f"SKILL.md references files that are not shipped: {missing}"


def test_our_parser_agrees_with_pyyaml_on_a_checked_in_corpus() -> None:
    """Differential check against the shapes that broke the old parser.

    This used to read `~/.claude/**/SKILL.md` and return early when that
    directory was absent — which it always is in CI. It asserted nothing there
    while looking like coverage: the same silent no-op this file had already
    fixed once, in a different disguise.

    The samples are checked in instead. Each is a shape the split-on-colon
    parser got wrong or would have: a quoted value (the one real-world file
    that caught the bug), a colon inside a value, a list-valued field, nested
    `metadata`, and a horizontal rule in the body that a naive delimiter search
    mistakes for the end of the frontmatter.
    """
    import yaml
    from xdog.agent.skills.manager import _parse_frontmatter

    corpus = sorted((Path(__file__).parent / "fixtures" / "frontmatter").glob("*.md"))
    assert len(corpus) >= 5, "the corpus went missing — this test is worthless without it"

    for path in corpus:
        text = path.read_text(encoding="utf-8")
        reference = yaml.safe_load(text.split("---", 2)[1])
        ours, body = _parse_frontmatter(text)

        for key, value in reference.items():
            if isinstance(value, str):
                assert ours.get(key) == value.strip(), f"{path.name}: {key}"
            elif isinstance(value, dict):
                # `metadata` entries are lifted to the top level for callers.
                for sub_key, sub_value in value.items():
                    assert ours.get(sub_key) == str(sub_value).strip(), f"{path.name}: {sub_key}"
        assert body.startswith(("Step one.", "Body.")), f"{path.name}: body mis-split"


def test_agent_keeps_skill_prefix_when_structured_prompt_is_rebuilt(tmp_path):
    from xdog.agent import Agent, AgentConfig
    from xdog.ai.types import AssistantMessage, SystemPromptBlock
    from xdog.ai.utils.event_stream import EventStream

    _mk(tmp_path, 'guard', '---\nname: guard\ndescription: guard\n---\nFIXED-SKILL-BODY')
    manager = SkillManager(tmp_path, packaged={})
    blocks = (SystemPromptBlock(text='STATIC', cache=True), SystemPromptBlock(text='MUTABLE', cache=False))
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()),
                  config=AgentConfig(system_prompt=blocks), skills=manager, active_skills=('guard',))
    assert agent.state.system_prompt[-2:] == blocks
    assert agent.state.system_prompt[0].cache is True
    assert 'FIXED-SKILL-BODY' in agent.state.system_prompt[0].text
    agent.set_system_prompt(blocks)
    assert len(agent.state.system_prompt) == 3
    agent.set_active_skills(())
    assert agent.state.system_prompt[-2:] == blocks
    assert 'FIXED-SKILL-BODY' not in str(agent.state.system_prompt)
