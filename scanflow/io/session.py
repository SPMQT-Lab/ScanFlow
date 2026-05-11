"""Session persistence: save and restore a ScanFlow working session."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Session:
    last_folder: str = ""
    last_recipe: str = ""
    theme: str = "dark"
    font_size: int = 11
    drift_channel: int = 0
    scan_log: list[str] = field(default_factory=list)

    _PATH = Path.home() / ".scanflow_session.json"

    def save(self, path: Optional[Path] = None) -> None:
        p = path or self._PATH
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Session":
        p = path or cls._PATH
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()
