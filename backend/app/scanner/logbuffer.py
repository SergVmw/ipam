"""Логи сканера: in-memory буфер с удержанием 1 час.

Для анализа работы (не работы) fping/nmap/TCP-пробы по всем сетям:
каждый завершённый скан добавляет запись (метод, exit-code, stderr,
список живых IP, счётчики, длительность). Записи старше RETENTION_S
выбрасываются лениво (при добавлении/чтении), в БД не пишутся —
после перезапуска контейнера лог чистый (это осознанно: «только час»).
"""
import time
from collections import deque

RETENTION_S = 3600      # 1 час — и ни секунды дольше
MAX_ENTRIES = 20000     # страховка от разбухания при плотных сканах


class ScanLogBuffer:
    def __init__(self, retention_s: int = RETENTION_S, max_entries: int = MAX_ENTRIES):
        self.retention_s = retention_s
        self.max_entries = max_entries
        self._entries: deque[dict] = deque()
        self._mono: deque[float] = deque()  # monotonic-метки времени записи

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.retention_s
        while self._mono and self._mono[0] < cutoff:
            self._mono.popleft()
            self._entries.popleft()

    def add(self, entry: dict) -> None:
        self._prune()
        self._entries.append(entry)
        self._mono.append(time.monotonic())
        while len(self._entries) > self.max_entries:
            self._entries.popleft()
            self._mono.popleft()

    def get(self, limit: int = 500) -> list[dict]:
        """Свежие записи, новые сначала (max limit)."""
        self._prune()
        limit = max(1, min(limit, 2000))
        return list(reversed(self._entries))[:limit]

    def __len__(self) -> int:
        self._prune()
        return len(self._entries)


# один буфер на процесс
scan_log = ScanLogBuffer()
