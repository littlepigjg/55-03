import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from ..models import BookMeta
from .models import (
    RegexRule,
    INVALID_FILENAME_CHARS,
    WINDOWS_RESERVED_NAMES,
)


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
