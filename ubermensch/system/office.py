"""Документы Word и таблицы Excel: Аня создаёт их сам, пользователь открывает в Office."""
from __future__ import annotations

import re
from pathlib import Path

import docx
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font


def write_word(path: Path, title: str, markdown: str) -> Path:
    """Простой Markdown → .docx: # заголовки, - списки, **жирный**, обычные абзацы."""
    document = docx.Document()
    if title:
        document.add_heading(title, level=0)
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if heading := re.match(r"^(#{1,3})\s+(.*)", stripped):
            document.add_heading(heading[2], level=len(heading[1]))
            continue
        bullet = re.match(r"^[-*•]\s+(.*)", stripped)
        paragraph = document.add_paragraph(style="List Bullet" if bullet else None)
        text = bullet[1] if bullet else stripped
        for part in re.split(r"(\*\*[^*]+\*\*)", text):
            if part.startswith("**") and part.endswith("**"):
                paragraph.add_run(part[2:-2]).bold = True
            elif part:
                paragraph.add_run(part)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path


def write_excel(path: Path, sheet: str, rows: list[list], append: bool = False) -> Path:
    """Лист таблицы: первая строка — заголовки. append — дописать строки в конец листа."""
    if path.exists():
        book = load_workbook(path)
    else:
        book = Workbook()
        book.remove(book.active)
    if sheet in book.sheetnames and not append:
        del book[sheet]
    fresh = sheet not in book.sheetnames  # новый лист — первая строка станет заголовками
    target = book.create_sheet(sheet) if fresh else book[sheet]
    for row in rows:
        target.append(row)
    if fresh and rows:
        for cell in target[1]:
            cell.font = Font(bold=True)
    for column in target.columns:  # ширина по содержимому
        width = max(len(str(c.value)) if c.value is not None else 0 for c in column)
        target.column_dimensions[column[0].column_letter].width = min(60, max(10, width + 2))
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return path


def read_excel(path: Path, max_rows: int = 200) -> dict[str, list[list]]:
    book = load_workbook(path, read_only=True, data_only=True)
    return {name: [list(r) for r in book[name].iter_rows(values_only=True)][:max_rows]
            for name in book.sheetnames}
