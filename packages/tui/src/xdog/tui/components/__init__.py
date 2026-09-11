"""TUI component package.

Re-exports all built-in components for convenient imports.
"""

from xdog.tui.components.box import Box, BoxComponent
from xdog.tui.components.cancellable_loader import CancellableLoader
from xdog.tui.components.container import Container
from xdog.tui.components.details import ExpandableComponent, set_details_expanded
from xdog.tui.components.details_panel import DetailsPanel
from xdog.tui.components.editor import Editor, EditorComponent, EditorOptions, EditorTheme
from xdog.tui.components.image import Image, ImageComponent, ImageOptions, ImageTheme
from xdog.tui.components.input import Input, InputComponent
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.components.loader import Loader, LoaderComponent
from xdog.tui.components.markdown import Markdown, MarkdownComponent
from xdog.tui.components.permission_panel import PermissionPanel
from xdog.tui.components.select_list import SelectListComponent
from xdog.tui.components.settings_list import SettingItem, SettingsList, SettingsListTheme
from xdog.tui.components.spacer import Spacer, SpacerComponent
from xdog.tui.components.status_line import StatusLine
from xdog.tui.components.text import Text, TextComponent
from xdog.tui.components.truncated_text import TruncatedTextComponent

__all__ = [
    "DetailsPanel",
    "InputPanel",
    "PermissionPanel",
    "StatusLine",
    "Box",
    "BoxComponent",
    "CancellableLoader",
    "Container",
    "Editor",
    "EditorComponent",
    "ExpandableComponent",
    "EditorOptions",
    "EditorTheme",
    "Image",
    "ImageComponent",
    "ImageOptions",
    "ImageTheme",
    "Input",
    "InputComponent",
    "Loader",
    "LoaderComponent",
    "Markdown",
    "MarkdownComponent",
    "SelectListComponent",
    "SettingItem",
    "SettingsList",
    "SettingsListTheme",
    "Spacer",
    "SpacerComponent",
    "Text",
    "TextComponent",
    "TruncatedTextComponent",
    "set_details_expanded",
]
