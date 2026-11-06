#!/usr/bin/env python3
"""Организатор файлов: раскладывает файлы папки по категориям.

Умеет находить дубликаты, работать в режиме наблюдения, отменять последний запуск
и сохранять отчёт. Использует только стандартную библиотеку Python 3.9+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CATEGORIES = {
    "Изображения": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic"},
    "Документы": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md"},
    "Таблицы": {".xls", ".xlsx", ".csv", ".ods"},
    "Презентации": {".ppt", ".pptx", ".odp"},
    "Архивы": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Видео": {".mp4", ".mkv", ".avi", ".mov", ".webm"},
    "Аудио": {".mp3", ".wav", ".flac", ".ogg", ".m4a"},
    "Программы": {".exe", ".msi", ".dmg", ".deb", ".apk"},
    "Код": {".py", ".js", ".html", ".css", ".cs", ".json", ".sql"},
}
OTHER = "Другое"
DUPLICATES = "Дубликаты"
TEMP_EXTENSIONS = {".crdownload", ".part", ".tmp", ".download"}  # файл ещё скачивается
LOG_NAME = ".organizer_log.json"
EXT_TO_CATEGORY = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}


@dataclass
class Move:
    src: Path
    dst: Path
    category: str
    size: int
    duplicate: bool = False


def category_of(path: Path) -> str:
    return EXT_TO_CATEGORY.get(path.suffix.lower(), OTHER)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_path(dst: Path, reserved: set[Path]) -> Path:
    """Если имя занято, добавляет « (1)», « (2)» и так далее."""
    candidate, n = dst, 1
    while candidate.exists() or candidate in reserved:
        candidate = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
        n += 1
    return candidate


def plan(folder: Path, by_date: bool = False, min_age: float = 30.0) -> list[Move]:
    """Строит список перемещений, ничего не трогая на диске."""
    now = time.time()
    files = []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.name.startswith(".") or p.suffix.lower() in TEMP_EXTENSIONS:
            continue
        st = p.stat()
        if now - st.st_mtime < min_age:  # файл только что изменили, возможно он ещё пишется
            continue
        files.append((p, st))

    # Хэшируем только файлы одинакового размера, это быстро даже для больших папок.
    by_size: dict[int, list[Path]] = defaultdict(list)
    for p, st in files:
        by_size[st.st_size].append(p)
    duplicates: set[Path] = set()
    for group in by_size.values():
        if len(group) < 2:
            continue
        seen: set[str] = set()
        for p in group:
            digest = sha256(p)
            if digest in seen:
                duplicates.add(p)
            seen.add(digest)

    reserved: set[Path] = set()
    moves: list[Move] = []
    for p, st in files:
        category = category_of(p)
        is_dup = p in duplicates
        target_dir = folder / category
        if by_date:
            target_dir /= datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m")
        same = target_dir / p.name
        if not is_dup and same.exists() and same.stat().st_size == st.st_size and sha256(same) == sha256(p):
            is_dup = True  # такой же файл уже лежит в нужной папке
        if is_dup:
            category, target_dir = DUPLICATES, folder / DUPLICATES
        dst = unique_path(target_dir / p.name, reserved)
        reserved.add(dst)
        moves.append(Move(p, dst, category, st.st_size, is_dup))
    return moves


def _read_log(folder: Path) -> list[dict]:
    path = folder / LOG_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except json.JSONDecodeError:
        return []


def _write_log(folder: Path, log: list[dict]) -> None:
    (folder / LOG_NAME).write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def apply(folder: Path, moves: list[Move]) -> None:
    """Перемещает файлы и записывает журнал, чтобы запуск можно было отменить."""
    done = []
    for m in moves:
        m.dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(m.src), str(m.dst))
        done.append({"src": str(m.src), "dst": str(m.dst)})
    if done:
        log = _read_log(folder)
        log.append({"time": datetime.now().isoformat(timespec="seconds"), "moves": done})
        _write_log(folder, log)


def undo(folder: Path) -> int:
    """Возвращает файлы последнего запуска на прежние места."""
    log = _read_log(folder)
    if not log:
        print("Нечего отменять.")
        return 1
    run = log.pop()
    restored = skipped = 0
    for mv in reversed(run["moves"]):
        src, dst = Path(mv["src"]), Path(mv["dst"])
        if not dst.exists() or src.exists():
            skipped += 1
            continue
        shutil.move(str(dst), str(src))
        restored += 1
        d = dst.parent
        while d != folder:  # убираем пустые папки, которые создала программа
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent
    _write_log(folder, log)
    print(f"Возвращено файлов: {restored}, пропущено: {skipped}.")
    return 0


def human(size: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def stats(moves: list[Move]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for m in moves:
        result[m.category][0] += 1
        result[m.category][1] += m.size
    return dict(sorted(result.items(), key=lambda kv: -kv[1][1]))


def summary_text(moves: list[Move]) -> str:
    if not moves:
        return "Нечего раскладывать."
    lines = [f"  {cat:<14}{n:>4} шт.  {human(size):>10}" for cat, (n, size) in stats(moves).items()]
    total = sum(m.size for m in moves)
    return "\n".join(lines + [f"  {'Всего':<14}{len(moves):>4} шт.  {human(total):>10}"])


def write_report(path: Path, folder: Path, moves: list[Move], dry_run: bool) -> None:
    mode = "пробный запуск" if dry_run else "выполнено"
    out = [f"# Отчёт организатора файлов", "",
           f"Папка: `{folder}`  ", f"Время: {datetime.now():%Y-%m-%d %H:%M}  ", f"Режим: {mode}", "",
           "| Категория | Файлов | Размер |", "|---|---:|---:|"]
    out += [f"| {cat} | {n} | {human(size)} |" for cat, (n, size) in stats(moves).items()]
    out += ["", "## Перемещения", ""]
    out += [f"- `{m.src.name}` → `{m.dst.relative_to(folder)}`" for m in moves]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def run_once(folder: Path, args: argparse.Namespace) -> list[Move]:
    moves = plan(folder, by_date=args.by_date, min_age=args.min_age)
    if not args.dry_run:
        apply(folder, moves)
    return moves


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Раскладывает файлы папки по категориям.")
    ap.add_argument("folder", type=Path, help="папка для наведения порядка, например ~/Downloads")
    ap.add_argument("--dry-run", action="store_true", help="только показать, что будет сделано")
    ap.add_argument("--by-date", action="store_true", help="внутри категорий делать папки по месяцам")
    ap.add_argument("--min-age", type=float, default=30.0,
                    help="не трогать файлы, изменённые менее N секунд назад (по умолчанию 30)")
    ap.add_argument("--watch", type=int, metavar="СЕК", help="работать постоянно, проверяя папку каждые N секунд")
    ap.add_argument("--undo", action="store_true", help="отменить последний запуск")
    ap.add_argument("--report", type=Path, help="сохранить отчёт в Markdown-файл")
    args = ap.parse_args(argv)

    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        print(f"Папка не найдена: {folder}", file=sys.stderr)
        return 1
    if args.undo:
        return undo(folder)

    if args.watch:
        print(f"Слежу за {folder} каждые {args.watch} с. Остановить: Ctrl+C.")
        try:
            while True:
                moves = run_once(folder, args)
                if moves:
                    print(f"{datetime.now():%H:%M:%S} разложено файлов: {len(moves)}")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nОстановлено.")
            return 0

    moves = run_once(folder, args)
    for m in moves:
        print(f"  {m.src.name}  →  {m.dst.relative_to(folder)}")
    print(("Пробный запуск, файлы не тронуты.\n" if args.dry_run else "Готово.\n") + summary_text(moves))
    if args.report:
        write_report(args.report, folder, moves, args.dry_run)
        print(f"Отчёт сохранён: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
