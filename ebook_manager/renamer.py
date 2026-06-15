import os
import re
import shutil
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable, Tuple

from .models import BookMeta


INVALID_FILENAME_CHARS = '<>:"/\\|?*'
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


@dataclass
class RegexRule:
    pattern: str
    replacement: str
    enabled: bool = True
    description: str = ""

    def apply(self, text: str) -> str:
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


class FileNameMetadataExtractor:
    _author_title_patterns = [
        re.compile(r'^(?P<author>[^-]+?)\s*[-–—]\s*(?P<title>.+?)$'),
        re.compile(r'^(?P<author>[^_]+?)_(?P<title>.+?)$'),
        re.compile(r'^【(?P<author>[^】]+)】(?P<title>.+?)$'),
        re.compile(r'^\[(?P<author>[^\]]+)\](?P<title>.+?)$'),
        re.compile(r'^(?P<author>[^\s]+)\s+(?P<title>.+?)$'),
    ]

    _year_pattern = re.compile(r'(?:(?:19|20)\d{2})')

    _edition_patterns = [
        re.compile(r'[【\[]([^】\]]*)[】\]]'),
        re.compile(r'\(([^)]*)\)'),
        re.compile(r'（([^）]*)）'),
    ]

    @classmethod
    def extract_from_filename(cls, file_path: str) -> Dict[str, str]:
        result = {"title": "", "author": "", "publish_year": "", "edition": ""}
        name = Path(file_path).stem

        year_match = cls._year_pattern.search(name)
        if year_match:
            result["publish_year"] = year_match.group(0)
            name = name.replace(year_match.group(0), "").strip()

        for pattern in cls._edition_patterns:
            for match in pattern.finditer(name):
                text = match.group(1).strip()
                if text and not result["edition"]:
                    result["edition"] = text

        cleaned_name = name
        for pattern in cls._edition_patterns:
            cleaned_name = pattern.sub('', cleaned_name)
        cleaned_name = re.sub(r'[_\-\s]+', ' ', cleaned_name).strip()

        for pattern in cls._author_title_patterns:
            m = pattern.match(cleaned_name)
            if m:
                author = m.group('author').strip()
                title = m.group('title').strip()
                if len(author) < 30 and len(title) > 0:
                    result["author"] = author
                    result["title"] = title
                    return result

        result["title"] = cleaned_name
        return result


class FileNameSanitizer:
    @staticmethod
    def sanitize(name: str, max_length: int = 200) -> str:
        if not name:
            return "untitled"

        for char in INVALID_FILENAME_CHARS:
            name = name.replace(char, '_')

        name = name.strip()
        name = name.rstrip('. ')

        path = Path(name)
        if path.stem.upper() in WINDOWS_RESERVED_NAMES:
            name = f"_{path.stem}{path.suffix}"

        if len(name) > max_length:
            if '.' in name:
                ext = Path(name).suffix
                stem = name[:max_length - len(ext)].rstrip('. ')
                name = stem + ext
            else:
                name = name[:max_length].rstrip('. ')

        if not name:
            name = "untitled"

        return name

    @staticmethod
    def fullwidth_to_halfwidth(text: str) -> str:
        result = []
        char_map = {
            '【': '[',
            '】': ']',
            '〔': '[',
            '〕': ']',
            '〖': '[',
            '〗': ']',
            '（': '(',
            '）': ')',
            '《': '<',
            '》': '>',
            '「': '"',
            '」': '"',
            '『': "'",
            '』': "'",
            '、': ',',
            '，': ',',
            '。': '.',
            '：': ':',
            '；': ';',
            '？': '?',
            '！': '!',
            '…': '...',
            '—': '-',
            '～': '~',
        }
        for char in text:
            if char in char_map:
                result.append(char_map[char])
            else:
                code = ord(char)
                if code == 0x3000:
                    result.append(' ')
                elif 0xFF01 <= code <= 0xFF5E:
                    result.append(chr(code - 0xFEE0))
                else:
                    result.append(char)
        return ''.join(result)


class FileNameTemplate:
    DEFAULT_TEMPLATE = "{author}-{title}-{publish_year}.{format}"

    _placeholder_pattern = re.compile(r'\{(\w+)\}')

    def __init__(self, template: str = DEFAULT_TEMPLATE):
        self.template = template

    def generate(self, book: BookMeta, regex_rules: Optional[List[RegexRule]] = None) -> str:
        context = self._build_context(book)
        name = self.template

        def replace_placeholder(match):
            key = match.group(1)
            return context.get(key, '')

        name = self._placeholder_pattern.sub(replace_placeholder, name)

        if regex_rules:
            for rule in regex_rules:
                name = rule.apply(name)

        name = re.sub(r'[-_\s]+', '-', name)
        name = name.strip('-_ ')

        ext = context.get('format', '')
        if ext and not name.lower().endswith(f'.{ext.lower()}'):
            name = f"{name}.{ext}"

        return name

    def _build_context(self, book: BookMeta) -> Dict[str, str]:
        year = ""
        if book.publish_date:
            year_match = re.search(r'(19|20)\d{2}', book.publish_date)
            if year_match:
                year = year_match.group(0)

        extracted = FileNameMetadataExtractor.extract_from_filename(book.file_path)

        return {
            "title": book.title or extracted.get("title", ""),
            "author": book.author or extracted.get("author", ""),
            "publisher": book.publisher or "",
            "publish_year": year or extracted.get("publish_year", ""),
            "publish_date": book.publish_date or "",
            "isbn": book.isbn or "",
            "language": book.language or "",
            "format": book.file_format or "",
            "edition": extracted.get("edition", ""),
        }

    @classmethod
    def get_available_placeholders(cls) -> List[Tuple[str, str]]:
        return [
            ("{title}", "书名"),
            ("{author}", "作者"),
            ("{publisher}", "出版社"),
            ("{publish_year}", "出版年份"),
            ("{publish_date}", "出版日期"),
            ("{isbn}", "ISBN"),
            ("{language}", "语言"),
            ("{format}", "文件格式"),
            ("{edition}", "版本/备注"),
        ]


class RenamePreviewGenerator:
    def __init__(self, template: Optional[FileNameTemplate] = None,
                 regex_rules: Optional[List[RegexRule]] = None):
        self.template = template or FileNameTemplate()
        self.regex_rules = regex_rules or []

    def generate_preview(self, books: List[BookMeta]) -> List[RenamePreviewItem]:
        items = []
        new_path_map: Dict[str, List[int]] = {}

        for i, book in enumerate(books):
            item = self._create_preview_item(book)
            items.append(item)

            new_path_lower = item.new_path.lower()
            if new_path_lower not in new_path_map:
                new_path_map[new_path_lower] = []
            new_path_map[new_path_lower].append(i)

        for indices in new_path_map.values():
            if len(indices) > 1:
                for idx in indices:
                    items[idx].has_conflict = True
                    items[idx].conflict_with = [
                        items[j].original_name for j in indices if j != idx
                    ]

        return items

    def _create_preview_item(self, book: BookMeta) -> RenamePreviewItem:
        original_path = book.file_path
        original_name = Path(original_path).name
        directory = Path(original_path).parent

        try:
            new_name = self.template.generate(book, self.regex_rules)
            new_name = FileNameSanitizer.fullwidth_to_halfwidth(new_name)
            new_name = FileNameSanitizer.sanitize(new_name)
            new_path = str(directory / new_name)

            will_change = (new_path.lower() != original_path.lower())

            return RenamePreviewItem(
                book=book,
                original_path=original_path,
                new_path=new_path,
                original_name=original_name,
                new_name=new_name,
                will_change=will_change,
            )
        except Exception as e:
            return RenamePreviewItem(
                book=book,
                original_path=original_path,
                new_path=original_path,
                original_name=original_name,
                new_name=original_name,
                will_change=False,
                error=str(e),
            )


class TransactionalRenamer:
    def __init__(self):
        self._backup_map: Dict[str, str] = {}
        self._rollback_stack: List[Tuple[str, str]] = []

    def rename(self, items: List[RenamePreviewItem],
               progress_callback: Optional[Callable[[int, int, str], None]] = None,
               pause_check: Optional[Callable[[], bool]] = None,
               cancel_check: Optional[Callable[[], bool]] = None) -> RenameResult:
        result = RenameResult(total=len(items))

        to_rename = [item for item in items if item.will_change and not item.has_conflict and not item.error]
        result.total = len(to_rename)

        self._rollback_stack = []
        self._backup_map = {}

        for i, item in enumerate(to_rename):
            if cancel_check and cancel_check():
                self._rollback()
                result.rolled_back = True
                result.error_message = "操作已取消，已回滚"
                return result

            if pause_check:
                while pause_check():
                    if cancel_check and cancel_check():
                        self._rollback()
                        result.rolled_back = True
                        result.error_message = "操作已取消，已回滚"
                        return result
                    import time
                    time.sleep(0.1)

            if progress_callback:
                progress_callback(i + 1, result.total, item.original_name)

            try:
                self._rename_file(item.original_path, item.new_path)
                self._rollback_stack.append((item.new_path, item.original_path))
                item.book.file_path = item.new_path
                result.renamed += 1
            except Exception as e:
                result.failed += 1
                result.failed_items.append((item.original_name, str(e)))
                self._rollback()
                result.rolled_back = True
                result.error_message = f"重命名失败，已回滚: {e}"
                result.success = False
                return result

        result.success = True
        return result

    def _rename_file(self, src: str, dst: str):
        if not os.path.exists(src):
            raise FileNotFoundError(f"源文件不存在: {src}")

        src_path = Path(src)
        dst_path = Path(dst)

        if dst_path.exists():
            raise FileExistsError(f"目标文件已存在: {dst}")

        long_src = self._make_long_path(src)
        long_dst = self._make_long_path(dst)

        try:
            os.rename(long_src, long_dst)
        except PermissionError:
            try:
                shutil.move(long_src, long_dst)
            except Exception:
                raise PermissionError(f"无法重命名文件，权限不足: {src}")
        except OSError as e:
            if "path too long" in str(e).lower() or len(dst) > 255:
                raise OSError(f"路径过长: {dst}")
            raise

    def _rollback(self):
        while self._rollback_stack:
            new_path, old_path = self._rollback_stack.pop()
            try:
                if os.path.exists(self._make_long_path(new_path)):
                    os.rename(
                        self._make_long_path(new_path),
                        self._make_long_path(old_path)
                    )
            except Exception:
                pass

    @staticmethod
    def _make_long_path(path: str) -> str:
        if os.name == 'nt':
            if not path.startswith('\\\\?\\'):
                path = os.path.abspath(path)
                if path.startswith('\\\\'):
                    path = '\\\\?\\UNC\\' + path[2:]
                else:
                    path = '\\\\?\\' + path
        return path


class DefaultRegexRules:
    @staticmethod
    def get_default_rules() -> List[RegexRule]:
        return [
            RegexRule(
                pattern=r'【[^】]*精校[^】]*】',
                replacement='',
                enabled=True,
                description='删除【精校版】等标记'
            ),
            RegexRule(
                pattern=r'[\[【][^\]】]*完[^\]】]*[\]】]',
                replacement='',
                enabled=True,
                description='删除【完结】[全本]等标记'
            ),
            RegexRule(
                pattern=r'（',
                replacement='(',
                enabled=True,
                description='全角左括号转半角'
            ),
            RegexRule(
                pattern=r'）',
                replacement=')',
                enabled=True,
                description='全角右括号转半角'
            ),
            RegexRule(
                pattern=r'【',
                replacement='[',
                enabled=True,
                description='全角左方括号转半角'
            ),
            RegexRule(
                pattern=r'】',
                replacement=']',
                enabled=True,
                description='全角右方括号转半角'
            ),
            RegexRule(
                pattern=r'\s+',
                replacement=' ',
                enabled=True,
                description='合并多个空格'
            ),
            RegexRule(
                pattern=r'[-_]{2,}',
                replacement='-',
                enabled=True,
                description='合并多个连字符'
            ),
        ]
