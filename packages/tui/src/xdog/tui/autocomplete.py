"""Autocomplete engine with pluggable completion sources.

Supports synchronous and asynchronous completion providers, fuzzy filtering,
configurable result limits, filesystem path completion, and slash commands.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, Sequence, runtime_checkable

from xdog.tui.fuzzy import fuzzy_filter


@dataclass(frozen=True, slots=True)
class CompletionItem:
    """A single completion suggestion.

    Attributes:
        text: The text to insert.
        label: Display label (defaults to *text* if empty).
        detail: Optional secondary description.
        sort_key: Optional explicit sort key for stable ordering.
    """

    text: str
    label: str = ""
    detail: str = ""
    sort_key: str = ""

    @property
    def display_label(self) -> str:
        return self.label or self.text


class CompletionProvider(Protocol):
    """Protocol for objects that supply completion candidates."""

    def get_completions(self, prefix: str) -> Sequence[CompletionItem]:
        """Return candidate completions for the current *prefix*."""
        ...


@runtime_checkable
class CompletionStatusProvider(Protocol):
    """Optional non-blocking progress information for an asynchronous provider."""

    def get_completion_status(self, prefix: str) -> str: ...


@dataclass
class StaticCompletionProvider:
    """A completion provider backed by a fixed list of items."""

    items: list[CompletionItem] = field(default_factory=list)

    def get_completions(self, prefix: str) -> Sequence[CompletionItem]:
        return self.items


@dataclass
class CallbackCompletionProvider:
    """A completion provider that delegates to a callback function."""

    callback: Callable[[str], Sequence[CompletionItem]]

    def get_completions(self, prefix: str) -> Sequence[CompletionItem]:
        return self.callback(prefix)


@dataclass(frozen=True, slots=True)
class SlashCommand:
    """A slash command definition for autocomplete."""

    name: str
    description: str = ""
    aliases: tuple[str, ...] = ()


@dataclass
class SlashCommandProvider:
    """Completion provider for ``/command`` entries."""

    commands: list[SlashCommand] = field(default_factory=list)

    def get_completions(self, prefix: str) -> Sequence[CompletionItem]:
        if not prefix.startswith("/"):
            return []
        query = prefix[1:].lower()
        results: list[CompletionItem] = []
        for cmd in self.commands:
            names = [cmd.name, *cmd.aliases]
            for name in names:
                if name.lower().startswith(query):
                    results.append(CompletionItem(
                        text=f"/{cmd.name}",
                        label=f"/{cmd.name}",
                        detail=cmd.description,
                        sort_key=cmd.name,
                    ))
                    break
        return results


@dataclass
class FileSystemCompletionProvider:
    """Completion provider for file system paths.

    Completes file and directory paths using ``os.scandir``.
    Handles ``~/`` expansion and adds trailing ``/`` for directories.
    """

    max_entries: int = 100

    def get_completions(self, prefix: str) -> Sequence[CompletionItem]:
        if not prefix:
            return []

        # Expand user home
        expanded = os.path.expanduser(prefix)
        path = Path(expanded)

        # Determine directory to scan and prefix to match
        if path.is_dir():
            scan_dir = path
            name_prefix = ""
        else:
            scan_dir = path.parent
            name_prefix = path.name

        if not scan_dir.is_dir():
            return []

        results: list[CompletionItem] = []
        try:
            entries = sorted(os.scandir(scan_dir), key=lambda e: e.name)
        except PermissionError:
            return []

        count = 0
        for entry in entries:
            if count >= self.max_entries:
                break
            if entry.name.startswith(".") and not name_prefix.startswith("."):
                continue
            if name_prefix and not entry.name.lower().startswith(name_prefix.lower()):
                continue

            # Build the completion text
            if prefix.startswith("~/"):
                base = "~/" + str(Path(entry.path).relative_to(Path.home()))
            else:
                base = entry.path

            if entry.is_dir():
                text = base + "/"
                detail = "directory"
            else:
                text = base
                detail = ""

            # Quote paths with spaces
            if " " in text and not text.startswith('"'):
                text = f'"{text}"'

            results.append(CompletionItem(
                text=text,
                label=entry.name + ("/" if entry.is_dir() else ""),
                detail=detail,
                sort_key=("0" if entry.is_dir() else "1") + entry.name,
            ))
            count += 1

        return results


@dataclass
class CombinedCompletionProvider:
    """Merges results from multiple providers, deduplicating by text."""

    providers: list[CompletionProvider] = field(default_factory=list)

    def get_completions(self, prefix: str) -> Sequence[CompletionItem]:
        seen: set[str] = set()
        results: list[CompletionItem] = []
        for provider in self.providers:
            for item in provider.get_completions(prefix):
                if item.text not in seen:
                    seen.add(item.text)
                    results.append(item)
        return results


@dataclass(frozen=True, slots=True)
class AutocompleteResult:
    """A scored autocomplete result, wrapping both the item and its fuzzy score."""

    item: CompletionItem
    score: int
    indices: tuple[int, ...]


@dataclass
class AutocompleteEngine:
    """Autocomplete engine that combines providers with fuzzy filtering.

    Attributes:
        providers: Registered completion providers.
        max_results: Maximum number of results to return.
        min_query_length: Minimum prefix length before triggering completions.
    """

    providers: list[CompletionProvider] = field(default_factory=list)
    max_results: int = 20
    min_query_length: int = 1

    def add_provider(self, provider: CompletionProvider) -> None:
        """Register a completion provider."""
        self.providers.append(provider)

    def remove_provider(self, provider: CompletionProvider) -> None:
        """Remove a previously registered provider."""
        self.providers = [p for p in self.providers if p is not provider]

    def complete(self, prefix: str) -> list[AutocompleteResult]:
        """Return scored completions for *prefix*.

        All providers are queried and their results are merged, fuzzy-filtered,
        and sorted by score.
        """
        if len(prefix) < self.min_query_length:
            return []

        # Gather all candidate items from every provider
        all_items: list[CompletionItem] = []
        for provider in self.providers:
            items = provider.get_completions(prefix)
            all_items.extend(items)

        if not all_items:
            return []

        # Build text -> items mapping for fuzzy matching
        text_to_items: dict[str, list[CompletionItem]] = {}
        for item in all_items:
            text_to_items.setdefault(item.display_label, []).append(item)

        candidates = list(text_to_items.keys())
        matches = fuzzy_filter(prefix, candidates, limit=self.max_results)

        results: list[AutocompleteResult] = []
        for m in matches:
            items = text_to_items.get(m.text, [])
            for item in items:
                results.append(
                    AutocompleteResult(
                        item=item,
                        score=m.score,
                        indices=m.indices,
                    )
                )

        # Sort by score descending, then by sort_key, then by text
        results.sort(
            key=lambda r: (-r.score, r.item.sort_key, r.item.text)
        )
        return results[: self.max_results]

    def complete_exact(self, prefix: str) -> list[AutocompleteResult]:
        """Return completions whose text starts exactly with *prefix*.

        No fuzzy matching -- only exact prefix matches.
        """
        if len(prefix) < self.min_query_length:
            return []

        results: list[AutocompleteResult] = []
        prefix_lower = prefix.lower()

        for provider in self.providers:
            for item in provider.get_completions(prefix):
                if item.display_label.lower().startswith(prefix_lower):
                    results.append(
                        AutocompleteResult(
                            item=item,
                            score=len(prefix),
                            indices=tuple(range(len(prefix))),
                        )
                    )

        results.sort(
            key=lambda r: (-r.score, r.item.sort_key, r.item.text)
        )
        return results[: self.max_results]
