"""Selection metadata is session-owned and fail-closed, never instruction history."""
import json

import pytest
from xdog.claw.core.persistence.skill_selection import load, save


def test_roundtrip_missing_and_maximum_selection(tmp_path):
    assert load(tmp_path, 'owner') == ()
    selected = tuple(f'skill-{index:03}-' + 'x' * 54 for index in range(128))
    save(tmp_path, 'owner', selected)
    assert load(tmp_path, 'owner') == selected
    assert load(tmp_path, 'other') == ()
    assert not list(tmp_path.glob('.skills-*'))


@pytest.mark.parametrize('value', [None, {'active': ['a', 'a']}, {'active': ['../escape']},
                                  {'active': [{}]}, {'active': ['a'], 'body': 'no'}, {'active': ['a'] * 129}])
def test_malformed_selection_is_rejected(tmp_path, value):
    (tmp_path / 'owner.skills.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load(tmp_path, 'owner')


def test_symlink_and_invalid_owner_rejected_without_overwrite(tmp_path):
    target = tmp_path / 'target'
    target.write_text('untouched')
    link = tmp_path / 'owner.skills.json'
    link.symlink_to(target)
    for operation in (lambda: load(tmp_path, 'owner'), lambda: save(tmp_path, 'owner', ('a',)),
                      lambda: save(tmp_path, '../other', ())):
        with pytest.raises(ValueError):
            operation()
    assert target.read_text() == 'untouched'
    target.unlink()
    with pytest.raises(ValueError):
        load(tmp_path, 'owner')


def test_oversized_selection_rejected(tmp_path):
    (tmp_path / 'owner.skills.json').write_text(' ' * 16385)
    with pytest.raises(ValueError):
        load(tmp_path, 'owner')
