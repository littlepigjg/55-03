from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..models import BookMeta


@dataclass
class RegexRule:
    pattern: str
    replacement: str
    enabled: bool = True
    description: str = ""

    def apply(self, text: str) -> str:
        import re
        if not self.enabled:
            return text
        try:
            return re.sub(self.pattern, self.replacement, text)
        except re.error:
            return text


@dataclass
class RenamePreviewItem:
    book: BookMeta
    original_path: str
    new_path: str
    original_name: str = ""
    new_name: str = ""
    has_conflict: bool = False
    conflict_with: List[str] = field(default_factory=list)
    will_change: bool = False
    error: Optional[str] = None


@dataclass
class RenameResult:
    success: bool = False
    total: int = 0
    renamed: int = 0
    failed: int = 0
    rolled_back: bool = False
    error_message: str = ""
    failed_items: List[Tuple[str, str]] = field(default_factory=list)


INVALID_FILENAME_CHARS = '<>:"/\\|?*'
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
