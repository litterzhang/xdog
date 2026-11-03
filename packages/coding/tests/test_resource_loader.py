"""Only explicitly supported project instructions are automatic context."""
import os

import pytest
from xdog.coding.core.resource_loader import discover_context_files


@pytest.mark.parametrize('name', [
    'CLAUDE.md', 'GEMINI.md', 'README.md', '.coding/INSTRUCTIONS.md', '.coding/instructions.md',
])
def test_other_project_files_are_not_automatic_instructions(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('Ordinary project file, not Coding configuration', encoding='utf-8')
    assert discover_context_files(tmp_path) == []


@pytest.mark.parametrize('name', ['CLAUDE.md', '.coding/INSTRUCTIONS.md', '.coding/instructions.md'])
async def test_other_config_can_be_read_explicitly_as_an_ordinary_file(tmp_path, name):
    from xdog.ai.types import TextContent
    from xdog.coding.core.tools import get_default_tools

    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('EXPLICIT_FILE_READ_MARKER', encoding='utf-8')
    tool = next(tool for tool in get_default_tools(tmp_path) if tool.name == 'filesystem')
    result = await tool.execute('explicit-read', {'action': 'read', 'path': str(path)})
    assert not result.is_error
    assert any('EXPLICIT_FILE_READ_MARKER' in part.text for part in result.content if isinstance(part, TextContent))


def test_discover_context_files_rejects_symlinked_instructions(tmp_path):
    target = tmp_path / 'outside-instructions.txt'
    target.write_text('Not an automatic instruction source', encoding='utf-8')
    (tmp_path / 'AGENTS.md').symlink_to(target)
    with pytest.raises(ValueError, match='symbolic|symlink'):
        discover_context_files(tmp_path)


def test_discover_agents_file(tmp_path):
    name = 'AGENTS.md'
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('Supported project instructions', encoding='utf-8')
    files = discover_context_files(tmp_path)
    assert len(files) == 1 and files[0].relative_path == name
    assert files[0].content == 'Supported project instructions'


def test_discover_context_files_rejects_large_without_dropping_constraints(tmp_path):
    large = tmp_path / 'AGENTS.md'
    large.write_text('x' * (65 * 1024), encoding='utf-8')
    with pytest.raises(ValueError, match='instruction|context'):
        discover_context_files(tmp_path)


def test_only_one_instruction_discovery_path(tmp_path):
    (tmp_path / 'AGENTS.md').write_text('Hello', encoding='utf-8')
    sources = discover_context_files(tmp_path)
    assert len(sources) == 1
    assert sources[0].relative_path == 'AGENTS.md' and sources[0].content == 'Hello'


@pytest.mark.parametrize('kind', ['directory', 'fifo', 'invalid-utf8'])
def test_invalid_existing_project_context_is_explicitly_unavailable(tmp_path, kind):
    path = tmp_path / 'AGENTS.md'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'fifo':
        os.mkfifo(path)
    else:
        path.write_bytes(b'\xff')
    with pytest.raises(ValueError, match='instruction|context'):
        discover_context_files(tmp_path)


def test_coding_directory_symlink_is_not_an_instruction_source(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'INSTRUCTIONS.md').write_text('Not an instruction source')
    (tmp_path / '.coding').symlink_to(outside, target_is_directory=True)
    assert discover_context_files(tmp_path) == []
    (tmp_path / 'AGENTS.md').write_text('Only supported instructions')
    assert [item.content for item in discover_context_files(tmp_path)] == ['Only supported instructions']


@pytest.mark.parametrize('kind', ['directory', 'fifo', 'invalid-utf8', 'oversized', 'dangling-link'])
def test_invalid_legacy_instruction_files_are_not_opened(tmp_path, kind):
    folder = tmp_path / '.coding'
    folder.mkdir()
    for name in ('INSTRUCTIONS.md', 'instructions.md'):
        path = folder / name
        if kind == 'directory':
            path.mkdir()
        elif kind == 'fifo':
            os.mkfifo(path)
        elif kind == 'invalid-utf8':
            path.write_bytes(b'\xff')
        elif kind == 'oversized':
            path.write_text('x' * (65 * 1024))
        else:
            path.symlink_to(tmp_path / 'missing')
    assert discover_context_files(tmp_path) == []
    (tmp_path / 'AGENTS.md').write_text('Actual instructions')
    assert [item.relative_path for item in discover_context_files(tmp_path)] == ['AGENTS.md']


def test_no_parent_directory_fallback(tmp_path):
    (tmp_path / 'AGENTS.md').write_text('Parent instructions are not automatic')
    child = tmp_path / 'child'
    child.mkdir()
    assert discover_context_files(child) == []
