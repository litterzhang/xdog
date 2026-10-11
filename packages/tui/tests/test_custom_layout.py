from __future__ import annotations

import pytest
from xdog.tui.components.custom_layout import (
    CustomBox,
    CustomChild,
    CustomColumn,
    CustomComponent,
    CustomPadding,
    CustomRow,
    CustomSpacer,
    CustomText,
    custom_frame,
)
from xdog.tui.tui import CURSOR_MARKER, Component
from xdog.tui.utils import string_width, strip_ansi


class Editor(Component):
    def __init__(self, text='界e\u0301', focused=True):
        self.text, self.focused, self.calls = text, focused, 0
    def render(self, width):
        self.calls += 1
        return ['\x1b[31m' + self.text + (CURSOR_MARKER if self.focused else '') + '\x1b[0m']


def test_fixed_and_weighted_rows_columns_padding_box_and_cursor_are_pure_bounded_data():
    editor = Editor()
    layout = CustomColumn((CustomChild(CustomText('Header'), size=1),
        CustomChild(CustomRow((CustomChild(CustomBox(CustomComponent(editor), title='编辑'), weight=2),
                               CustomChild(CustomBox(CustomText('Status'), title='Side'), weight=1)), gap=1))), gap=1)
    value = custom_frame(CustomPadding(layout, left=1, top=1), 40, 10)
    assert len(value['lines']) == 10 and all(string_width(line) <= 40 for line in value['lines'])
    assert value['cursor'] == [4, 5]  # padding + fixed header/gap + border + 3-cell glyph
    assert editor.calls == 1 and 'Header' in strip_ansi('\n'.join(value['lines']))
    assert '编辑' in strip_ansi('\n'.join(value['lines']))
    assert not any(CURSOR_MARKER in line for line in value['lines'])
    assert custom_frame(layout, 40, 0) == {'lines': [], 'cursor': None}
    assert custom_frame(layout, 0, 10) == {'lines': [], 'cursor': None}
    assert editor.calls == 1


def test_unicode_styles_alignment_wrap_and_terminal_controls_do_not_leak_between_columns():
    value = custom_frame(CustomRow((
        CustomChild(CustomText('\x1b[31m界e\u0301\x1b]52;c;bad\a\x1b[2J'), size=4),
        CustomChild(CustomText('ok', align='right'))), gap=1), 10, 2)
    assert string_width(value['lines'][0]) == 10
    assert '\x1b]52' not in ''.join(value['lines']) and '\x1b[2J' not in ''.join(value['lines'])
    assert '\x1b[0m' in value['lines'][0] and strip_ansi(value['lines'][0]).endswith('ok')
    wrapped = custom_frame(CustomText('界界👩\u200d💻e\u0301 tail', wrap=True), 4, 10)
    assert all(string_width(line) <= 4 for line in wrapped['lines'])
    assert '👩\u200d💻' in ''.join(wrapped['lines']) and 'e\u0301' in ''.join(wrapped['lines'])


@pytest.mark.parametrize('layout', [CustomRow((CustomChild(CustomText('x'), size=True),)),
    CustomColumn((CustomChild(CustomText('x'), weight=0),)), CustomRow((), gap=-1),
    CustomText('x', wrap=1), CustomText('x', align='bad'), CustomPadding(CustomText('x'), left=65),
    CustomText('x' * (128 * 1024 + 1)), CustomText('\ud800'),
    CustomRow(tuple(CustomChild(CustomSpacer()) for _ in range(33)))])
def test_invalid_or_excessive_layouts_are_rejected(layout):
    with pytest.raises((ValueError, UnicodeError)):
        custom_frame(layout, 80, 20)


def test_cycles_depth_duplicate_focus_and_component_output_limits_are_rejected():
    deep = CustomText('x')
    for _ in range(13):
        deep = CustomBox(deep)
    with pytest.raises(ValueError, match='depth'):
        custom_frame(deep, 80, 30)
    cycle = CustomPadding(CustomText('x'))
    object.__setattr__(cycle, 'content', cycle)
    with pytest.raises(ValueError, match='cycle'):
        custom_frame(cycle, 80, 20)
    duplicate = CustomRow((CustomChild(CustomComponent(Editor())), CustomChild(CustomComponent(Editor()))))
    with pytest.raises(ValueError, match='multiple'):
        custom_frame(duplicate, 40, 4)
    with pytest.raises(ValueError, match='line'):
        custom_frame(CustomComponent(Editor('x\ny')), 40, 4)
    with pytest.raises(ValueError, match='output'):
        custom_frame(CustomText('x' * 9000), 10000, 1)


@pytest.mark.parametrize('width,height', [(1, 1), (2, 2), (3, 3), (24, 4), (80, 8)])
def test_narrow_layouts_never_render_outside_the_budget(width, height):
    value = custom_frame(CustomRow((CustomChild(CustomBox(CustomText('界界'))),
                                    CustomChild(CustomPadding(CustomText('other'), left=2))), gap=4), width, height)
    assert len(value['lines']) <= height and all(string_width(line) <= width for line in value['lines'])
    assert value['cursor'] is None


def test_largest_remainder_and_fixed_clipping_have_deterministic_cell_allocations():
    value = custom_frame(CustomRow((CustomChild(CustomText('A' * 20), weight=2),
                                   CustomChild(CustomText('B' * 20), weight=1)), gap=1), 11, 1)
    assert strip_ansi(value['lines'][0]) == 'AAAAAAA BBB'
    narrow = custom_frame(CustomRow((CustomChild(CustomText('left'), size=5),
                                    CustomChild(CustomText('right'), size=5))), 3, 1)
    assert strip_ansi(narrow['lines'][0]) == 'lef'


def test_total_node_budget_and_hidden_multiple_component_markers_cannot_escape_validation():
    tree = CustomColumn(tuple(CustomChild(CustomRow(tuple(CustomChild(CustomText('x')) for _ in range(32))))
                              for _ in range(9)))
    with pytest.raises(ValueError, match='node'):
        custom_frame(tree, 80, 20)
    class Bad(Component):
        def render(self, width):
            return ['visible', 'hidden' + CURSOR_MARKER, 'hidden too' + CURSOR_MARKER]
    with pytest.raises(ValueError, match='multiple'):
        custom_frame(CustomComponent(Bad()), 80, 1)


def test_viewport_scrolls_beyond_host_height_clamps_on_resize_and_composes():
    from xdog.tui.components.custom_layout import CustomViewport

    document = '\n'.join(f'row-{row:04d}---tail' for row in range(320))
    view = CustomViewport(CustomText(document), width=24, height=320, top=300, left=9)
    frame = custom_frame(CustomBox(view, title='Scroll'), 8, 5)
    assert strip_ansi(frame['lines'][1]) == '│--tail│'
    assert len(frame['lines']) == 5 and frame['cursor'] is None
    resized = custom_frame(CustomViewport(CustomText(document), 24, 320, top=320, left=24), 30, 200)
    assert strip_ansi(resized['lines'][0]).startswith('row-0120')
    assert strip_ansi(resized['lines'][-1]).startswith('row-0319')
    assert custom_frame(view, 0, 10) == {'lines': [], 'cursor': None}


def test_viewport_crops_whole_graphemes_even_with_styles_inside_zwj_or_combining_sequences():
    from xdog.tui.components.custom_layout import CustomViewport

    document = '\x1b[31m界e\x1b[1m\u0301👩\x1b[32m\u200d💻tail\x1b]52;c;bad\a'
    view = CustomViewport(CustomText(document), 20, 1, left=1)
    frame = custom_frame(view, 4, 1)
    assert strip_ansi(frame['lines'][0]) == ' e\u0301👩\u200d💻'
    assert '\x1b[31m' in frame['lines'][0] and '\x1b[32m' in frame['lines'][0]
    assert '\x1b]52' not in frame['lines'][0]
    cut_right = custom_frame(CustomViewport(CustomText(document), 20, 1), 4, 1)
    assert strip_ansi(cut_right['lines'][0]) == '界e\u0301 '
    assert string_width(cut_right['lines'][0]) == 4


def test_viewport_component_cursor_translates_hides_and_recovers_with_resize():
    from xdog.tui.components.custom_layout import CustomViewport

    class Canvas(Component):
        def render(self, width):
            return ['row'] * 250 + ['012345' + CURSOR_MARKER + 'tail']
    canvas = Canvas()
    view = CustomViewport(CustomComponent(canvas), 32, 300, top=248, left=3)
    assert custom_frame(view, 8, 4)['cursor'] == [2, 3]
    assert custom_frame(view, 8, 2)['cursor'] is None
    assert custom_frame(CustomViewport(CustomComponent(canvas), 32, 300, top=248, left=8), 8, 4)['cursor'] is None
    editor = Editor()
    hidden = CustomViewport(CustomComponent(editor), 32, 300, top=20)
    assert custom_frame(hidden, 8, 4)['cursor'] is None
    assert editor.calls == 1
    assert custom_frame(hidden, 0, 4) == {'lines': [], 'cursor': None}
    assert custom_frame(hidden, 8, 0) == {'lines': [], 'cursor': None}
    assert editor.calls == 1


@pytest.mark.parametrize('arguments', [dict(width=0, height=1), dict(width=1, height=0),
    dict(width=True, height=1), dict(width=8193, height=1), dict(width=1, height=4097),
    dict(width=8192, height=200), dict(width=20, height=20, top=-1),
    dict(width=20, height=20, left=21), dict(width=20, height=20, top=True)])
def test_viewport_rejects_invalid_or_excessive_canvases(arguments):
    from xdog.tui.components.custom_layout import CustomViewport

    with pytest.raises(ValueError, match='viewport'):
        custom_frame(CustomViewport(CustomText('x'), **arguments), 80, 20)


def test_viewport_aggregate_and_invisible_component_retention_budgets_stay_bounded():
    from xdog.tui.components.custom_layout import CustomViewport

    view = CustomViewport(CustomSpacer(), 512, 256)
    with pytest.raises(ValueError, match='aggregate'):
        custom_frame(CustomColumn(tuple(CustomChild(view) for _ in range(5))), 80, 20)
    class Excessive(Component):
        def render(self, width):
            return ['\x1b[31m' * 1000] * 300
    with pytest.raises(ValueError, match='retained'):
        custom_frame(CustomViewport(CustomComponent(Excessive()), 32, 300, top=299), 8, 1)


def test_stack_rectangles_blank_full_footprint_preserve_styles_and_clip_whole_graphemes():
    from xdog.tui.components.custom_layout import CustomLayer, CustomStack

    layout = CustomStack(CustomText('\x1b[31m界e\x1b[1m\u0301👩\x1b[32m\u200d💻tail'),
                         (CustomLayer(CustomText('Z\x1b]52;c;bad\a'), left=1, width=3, height=2),))
    value = custom_frame(layout, 10, 3)
    assert string_width(value['lines'][0]) == 10
    assert strip_ansi(value['lines'][0]).startswith(' Z   tail')
    assert strip_ansi(value['lines'][1]) == ' ' * 10  # Opaque blank rows are not transparency.
    assert '\x1b]52' not in ''.join(value['lines']) and value['cursor'] is None
    assert '\x1b[32m' in value['lines'][0] and '\x1b[0m' in value['lines'][0]


def test_stack_cursor_occlusion_translation_hidden_components_and_visible_focus_conflicts():
    from xdog.tui.components.custom_layout import CustomLayer, CustomStack

    lower, upper, hidden = Editor('abc'), Editor('x'), Editor('hidden')
    layout = CustomStack(CustomComponent(lower), (
        CustomLayer(CustomComponent(upper), width=5, height=1),
        CustomLayer(CustomComponent(hidden), left=100, width=2, height=1)))
    assert custom_frame(layout, 10, 3)['cursor'] == [0, 1]
    assert lower.calls == upper.calls == 1 and hidden.calls == 0
    assert custom_frame(layout, 0, 3) == {'lines': [], 'cursor': None}
    assert upper.calls == 1
    covered = CustomStack(CustomComponent(lower), (CustomLayer(CustomSpacer(), width=5, height=1),))
    assert custom_frame(covered, 10, 3)['cursor'] is None
    conflict = CustomStack(CustomComponent(lower), (CustomLayer(CustomComponent(upper), left=5, width=5, height=1),))
    with pytest.raises(ValueError, match='multiple'):
        custom_frame(conflict, 10, 3)


@pytest.mark.parametrize('rectangle', [dict(left=-1), dict(top=True), dict(width=False),
    dict(height=4097), dict(left=10001), dict(top=4097), dict(width=10001)])
def test_stack_invalid_rectangles_are_not_coerced_or_rendered(rectangle):
    from xdog.tui.components.custom_layout import CustomLayer, CustomStack

    with pytest.raises(ValueError, match='layer'):
        custom_frame(CustomStack(CustomText('x'), (CustomLayer(CustomText('y'), **rectangle),)), 80, 20)


def test_stack_nested_bounds_and_offscreen_layer_can_recover_after_resize():
    from xdog.tui.components.custom_layout import CustomLayer, CustomStack

    component = Editor('x')
    layout = CustomStack(CustomSpacer(), (CustomLayer(CustomBox(CustomComponent(component)), left=8, top=1,
                                                     width=8, height=3),))
    assert custom_frame(layout, 8, 4)['cursor'] is None and component.calls == 0
    assert custom_frame(layout, 20, 5)['cursor'] == [2, 10]
    with pytest.raises(ValueError, match='stack'):
        custom_frame(CustomStack(CustomText('x'), tuple(CustomLayer(CustomText('y')) for _ in range(33))), 80, 20)
    cycle = CustomStack(CustomText('x'))
    object.__setattr__(cycle, 'background', cycle)
    with pytest.raises(ValueError, match='cycle'):
        custom_frame(cycle, 80, 20)
