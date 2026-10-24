"""Run with python -I -S against an installed wheel target; no editable fallbacks.

Arguments: TARGET THIRD_PARTY_DEPENDENCIES EXAMPLE [--expect-flow].
The caller supplies dependencies without XDOG packages/metadata or .pth files.
Only explicit package roots are added to sys.path. This is an offline artifact
probe, not an ordinary pytest test or a fresh dependency-resolution test.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import runpy
import sys
import tempfile
from importlib import metadata
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("dependencies", type=Path)
    parser.add_argument("example", type=Path)
    parser.add_argument("--expect-flow", action="store_true")
    args = parser.parse_args()
    target = args.target.resolve()
    dependencies = args.dependencies.resolve()
    assert sys.flags.isolated and sys.flags.no_site, "Run using python -I -S"
    assert not list(dependencies.glob("*.pth")), "Do not expose editable import hooks"
    assert not any(p.name.startswith("xdog") for p in dependencies.iterdir())
    sys.path[:0] = [str(target), str(dependencies)]

    import xdog.agent
    import xdog.agent.skills
    import xdog.ai
    from xdog.agent.skills import SkillRepo, packaged_skills

    for module in (xdog.agent, xdog.ai):
        assert module.__file__ and Path(module.__file__).resolve().is_relative_to(target)
    for name in ("xdog-ai", "xdog-agent"):
        assert Path(str(metadata.distribution(name).locate_file(""))).resolve() == target
    requires = metadata.requires("xdog-agent") or []
    assert not any(item.startswith("xdog-flow") for item in requires)
    discovered = packaged_skills()
    skill_hashes = {
        slug: {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }
        for slug, root in discovered.items()
    }
    example = runpy.run_path(str(args.example.resolve()))
    with tempfile.TemporaryDirectory(prefix="skill-artifact-") as directory:
        root = Path(directory)
        if args.expect_flow:
            import xdog.flow

            assert xdog.flow.__file__ and Path(xdog.flow.__file__).resolve().is_relative_to(target)
            assert Path(str(metadata.distribution("xdog-flow").locate_file(""))).resolve() == target
            assert set(discovered) == {"flow"}, discovered
            skill_dir = discovered["flow"]
            assert skill_dir.resolve().is_relative_to(target)
            assert len(list((skill_dir / "examples").glob("*.json"))) == 12
            result = asyncio.run(
                example["demonstrate"](
                    SkillRepo(builtin=True).register(directory=root / "unused", name="shared"), "flow"
                )
            )
            assert result["packaged"] is True
            assert not (root / "unused").exists()
        else:
            assert importlib.util.find_spec("xdog.flow") is None
            assert discovered == {}, discovered
            custom = root / "skills" / "procedure"
            custom.mkdir(parents=True)
            (custom / "SKILL.md").write_text(
                "---\nname: procedure\ndescription: Local example\n---\nDISTINCT_LOCAL_SKILL_BODY\n",
                encoding="utf-8",
            )
            result = asyncio.run(
                example["demonstrate"](
                    SkillRepo(builtin=True).register(directory=custom.parent, name="shared"), "procedure"
                )
            )
            assert result["packaged"] is False
    print(
        json.dumps(
            {
                "target": str(target),
                "flow_expected": args.expect_flow,
                "discovered": sorted(discovered),
                "imports_from_wheel_target": True,
                "editable_fallback_disabled": True,
                "agent_requires_flow": False,
                "example": result,
                "verification_scope": "installed baseline session activation and packaged assets only",
                "readonly_loader_present": hasattr(xdog.agent.skills, "create_skill_tool"),
                "f1_lifecycle_or_snapshot_gate_complete": False,
                "skill_hashes": skill_hashes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
