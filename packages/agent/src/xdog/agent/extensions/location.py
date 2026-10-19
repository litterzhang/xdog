"""Detached extension-root preparation; no worker, policy or activation state."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExtensionRoot:
    requested: Path
    canonical: Path

    @classmethod
    def read(cls, root: Path) -> ExtensionRoot:
        return cls(root, root.resolve())

    def adopt(self, root: Path) -> Path:
        if (self.requested != root or not self.canonical.is_absolute()
                or '..' in self.canonical.parts):
            raise ValueError('Prepared extension root belongs to another configured location')
        return self.canonical
