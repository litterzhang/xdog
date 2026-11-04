"""TUI component package.

Re-exports all built-in components for convenient imports.
"""

from xdog.tui.components.attachment_panel import AttachmentPanel
from xdog.tui.components.box import Box
from xdog.tui.components.cancellable_loader import CancellableLoader
from xdog.tui.components.container import Container
from xdog.tui.components.details import ExpandableComponent, set_details_expanded
from xdog.tui.components.details_panel import DetailsPanel
from xdog.tui.components.editor import Editor, EditorOptions, EditorTheme
from xdog.tui.components.image import Image, ImageOptions, ImageTheme
from xdog.tui.components.input import Input
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.components.loader import Loader
from xdog.tui.components.markdown import Markdown
from xdog.tui.components.navigation_panel import NavigationPanel
from xdog.tui.components.permission_panel import PermissionPanel
from xdog.tui.components.select_list import SelectList
from xdog.tui.components.settings_list import SettingItem, SettingsList, SettingsListTheme
from xdog.tui.components.spacer import Spacer
from xdog.tui.components.status_line import StatusLine
from xdog.tui.components.text import Text
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.components.truncated_text import TruncatedText

__all__ = [
    "TruncatedText",
    "SelectList",
    "AttachmentPanel",
    "TranscriptPanel",
    "DetailsPanel",
    "InputPanel",
    "PermissionPanel",
    "NavigationPanel",
    "StatusLine",
    "Box",
    "CancellableLoader",
    "Container",
    "Editor",
    "ExpandableComponent",
    "EditorOptions",
    "EditorTheme",
    "Image",
    "ImageOptions",
    "ImageTheme",
    "Input",
    "Loader",
    "Markdown",
    "SettingItem",
    "SettingsList",
    "SettingsListTheme",
    "Spacer",
    "Text",
    "set_details_expanded",
]
