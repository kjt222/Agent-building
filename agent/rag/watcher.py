from __future__ import annotations

from pathlib import Path
from typing import Iterable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .service import RagService


class RagEventHandler(FileSystemEventHandler):
    def __init__(self, service: RagService, extensions: Iterable[str]) -> None:
        super().__init__()
        self.service = service
        self.extensions = {ext.lower() for ext in extensions}

    def _should_process(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in self.extensions

    def on_created(self, event) -> None:  # type: ignore[override]
        path = Path(event.src_path)
        if self._should_process(path):
            self.service.index_path(path)

    def on_modified(self, event) -> None:  # type: ignore[override]
        path = Path(event.src_path)
        if self._should_process(path):
            self.service.index_path(path)

    def on_moved(self, event) -> None:  # type: ignore[override]
        src = Path(event.src_path)
        dest = Path(event.dest_path)
        if self._should_process(src):
            self.service.remove_path(src)
        if self._should_process(dest):
            self.service.index_path(dest)

    def on_deleted(self, event) -> None:  # type: ignore[override]
        path = Path(event.src_path)
        if path.suffix.lower() in self.extensions:
            self.service.remove_path(path)


def watch_path(service: RagService, path: Path) -> Observer:
    handler = RagEventHandler(service, service.config.extensions)
    observer = Observer()
    observer.schedule(handler, str(path), recursive=True)
    observer.start()
    return observer
