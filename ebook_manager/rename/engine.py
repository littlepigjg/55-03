import os
import shutil
import time
from pathlib import Path
from typing import List, Dict, Optional, Callable, Tuple

from ..models import BookMeta
from .models import RenamePreviewItem, RenameResult
from .name_utils import (
    FileNameTemplate,
    FileNameSanitizer,
    FileNameMetadataExtractor,
)


class RenamePreviewGenerator:
    def __init__(self, template: Optional[FileNameTemplate] = None,
                 regex_rules: Optional[list] = None):
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


class RenameTransaction:
    def __init__(self):
        self._history: List[Tuple[RenamePreviewItem, str, str]] = []

    @property
    def is_empty(self) -> bool:
        return len(self._history) == 0

    def add(self, item: RenamePreviewItem, old_path: str, new_path: str):
        self._history.append((item, old_path, new_path))

    def commit_all(self) -> int:
        return len(self._history)

    def rollback_all(self) -> int:
        count = 0
        while self._history:
            item, old_path, new_path = self._history.pop()
            disk_ok = False
            try:
                long_new = self._make_long_path(new_path)
                long_old = self._make_long_path(old_path)
                if os.path.exists(long_new):
                    os.rename(long_new, long_old)
                    disk_ok = True
                    count += 1
            except Exception:
                pass
            item.book.file_path = old_path
        return count

    def rollback_one(self) -> Optional[Tuple[str, str]]:
        if not self._history:
            return None
        item, old_path, new_path = self._history.pop()
        try:
            long_new = self._make_long_path(new_path)
            long_old = self._make_long_path(old_path)
            if os.path.exists(long_new):
                os.rename(long_new, long_old)
        except Exception:
            pass
        item.book.file_path = old_path
        return (old_path, new_path)

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


class TransactionalRenamer:
    def __init__(self):
        self._transaction = RenameTransaction()

    @property
    def transaction(self) -> RenameTransaction:
        return self._transaction

    def rename(self, items: List[RenamePreviewItem],
               progress_callback: Optional[Callable[[int, int, str], None]] = None,
               pause_check: Optional[Callable[[], bool]] = None,
               cancel_check: Optional[Callable[[], bool]] = None) -> RenameResult:
        result = RenameResult()

        to_rename = [
            item for item in items
            if item.will_change and not item.has_conflict and not item.error
        ]
        result.total = len(to_rename)

        self._transaction = RenameTransaction()

        for i, item in enumerate(to_rename):
            if cancel_check and cancel_check():
                self._rollback_all()
                result.rolled_back = True
                result.error_message = "操作已取消，已回滚"
                return result

            if pause_check:
                while pause_check():
                    if cancel_check and cancel_check():
                        self._rollback_all()
                        result.rolled_back = True
                        result.error_message = "操作已取消，已回滚"
                        return result
                    time.sleep(0.1)

            if progress_callback:
                progress_callback(i + 1, result.total, item.original_name)

            try:
                self._do_rename(item)
                result.renamed += 1
            except Exception as e:
                result.failed += 1
                result.failed_items.append((item.original_name, str(e)))
                self._rollback_all()
                result.rolled_back = True
                result.error_message = f"重命名失败，已回滚: {e}"
                return result

        result.success = True
        return result

    def _do_rename(self, item: RenamePreviewItem):
        src = item.original_path
        dst = item.new_path

        if not os.path.exists(src):
            raise FileNotFoundError(f"源文件不存在: {src}")

        dst_path = Path(dst)
        if dst_path.exists():
            raise FileExistsError(f"目标文件已存在: {dst}")

        long_src = RenameTransaction._make_long_path(src)
        long_dst = RenameTransaction._make_long_path(dst)

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

        self._transaction.add(item, src, dst)
        item.book.file_path = dst

    def _rollback_all(self):
        self._transaction.rollback_all()
