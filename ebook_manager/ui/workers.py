import time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal, QMutex, QMutexLocker

from ..models import BookMeta
from ..scanner import BookshelfScanner
from ..metadata_parser import MetadataParser
from ..renamer import TransactionalRenamer, RenamePreviewItem, RenameResult


class ScanWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(list)

    def __init__(self, directories: list, recursive: bool):
        super().__init__()
        self._directories = directories
        self._recursive = recursive

    def run(self):
        scanner = BookshelfScanner()
        scanner.set_progress_callback(
            lambda c, t, p: self.progress.emit(c, t, p)
        )
        files = scanner.scan_directories(self._directories, self._recursive)
        self.finished_signal.emit(files)


class ParseWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(list)

    def __init__(self, files: list):
        super().__init__()
        self._files = files

    def run(self):
        parser = MetadataParser()
        books = []
        total = len(self._files)
        for i, f in enumerate(self._files):
            self.progress.emit(i + 1, total, f)
            try:
                book = parser.parse(f)
                books.append(book)
            except Exception:
                books.append(
                    BookMeta(
                        file_path=f,
                        file_format=Path(f).suffix.lstrip("."),
                        title=Path(f).stem,
                    )
                )
        self.finished_signal.emit(books)


class RenameWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(object)
    paused_changed = pyqtSignal(bool)

    def __init__(self, items: list):
        super().__init__()
        self._items = items
        self._paused = False
        self._cancelled = False
        self._mutex = QMutex()

    def pause(self):
        with QMutexLocker(self._mutex):
            self._paused = True
        self.paused_changed.emit(True)

    def resume(self):
        with QMutexLocker(self._mutex):
            self._paused = False
        self.paused_changed.emit(False)

    def cancel(self):
        with QMutexLocker(self._mutex):
            self._cancelled = True
            self._paused = False

    def is_paused(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._paused

    def is_cancelled(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._cancelled

    def run(self):
        renamer = TransactionalRenamer()
        result = renamer.rename(
            self._items,
            progress_callback=lambda c, t, n: self.progress.emit(c, t, n),
            pause_check=lambda: self.is_paused(),
            cancel_check=lambda: self.is_cancelled(),
        )
        self.finished_signal.emit(result)
