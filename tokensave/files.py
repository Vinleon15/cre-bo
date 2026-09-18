"""Разбор файлов один раз на версию + три уровня подачи в контекст.

Оригинал файла не изменяется и не перемещается. Ключ кэша — sha256 содержимого
плюс версия парсера: переименование файла кэш не ломает, а правка внутри —
ломает, как и должна. Имя файла и mtime как ключ не используются никогда.

Отдельная забота — места, где данные обычно теряются: сноски PDF, скрытые
листы Excel, заметки докладчика PPTX, комментарии Word. Они извлекаются
всегда и в режиме targeted подаются целиком.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

PARSER_VERSION = 1

# Куски, которые нельзя выбрасывать при отборе: в них живут цифры и условия.
CRITICAL_RE = re.compile(
    r"\d[\d\s.,]*\s*(?:%|руб|₽|\$|€|тыс|млн|млрд|кв\.?\s*м|м2)"      # суммы, площади
    r"|\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b"                          # даты
    r"|\bне позднее\b|\bв течение\b|\bсрок\b"                          # сроки
    r"|\bкроме\b|\bза исключением\b|\bесли иное\b|\bпри условии\b"     # исключения
    r"|\bне вправе\b|\bобязан\b|\bштраф\b|\bпеня\b",
    re.I,
)
# Куски из «теряемых» слоёв документа — подаются целиком независимо от релевантности.
ALWAYS_INCLUDE_KINDS = {"footnote", "comment", "notes", "hidden", "formula"}


@dataclass
class Chunk:
    idx: int
    kind: str       # page / sheet / slide / notes / footnote / comment / hidden / formula / text
    locator: str    # "стр. 12", "лист 'Допущения'", "слайд 4 (заметки)"
    text: str
    critical: bool = False


@dataclass
class Document:
    hash: str
    path: str
    kind: str
    chunks: list[Chunk]
    meta: dict

    @property
    def outline(self) -> str:
        lines = [f"{c.locator}: {c.text[:90].strip()}" for c in self.chunks[:200]]
        return "\n".join(lines)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _mk(idx: int, kind: str, locator: str, text: str) -> Chunk:
    text = text.strip()
    return Chunk(idx, kind, locator, text, critical=bool(CRITICAL_RE.search(text)))


# ---------------------------------------------------------------- парсеры

def _parse_pdf(path: Path) -> tuple[list[Chunk], dict]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    chunks, empty = [], 0
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if not text.strip():
            empty += 1
        chunks.append(_mk(len(chunks), "page", f"стр. {i}", text))
    meta = {"pages": len(reader.pages), "empty_pages": empty}
    # Пустой текстовый слой — это скан. Молча считать документ пустым нельзя.
    if reader.pages and empty / len(reader.pages) > 0.5:
        meta["warning"] = ("у большинства страниц нет текстового слоя — это скан. "
                           "Нужен OCR, иначе документ считается непрочитанным")
    return chunks, meta


def _parse_docx(path: Path) -> tuple[list[Chunk], dict]:
    import docx

    doc = docx.Document(str(path))
    chunks = []
    body = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if body:
        chunks.append(_mk(0, "text", "текст документа", body))
    for ti, table in enumerate(doc.tables, 1):
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        chunks.append(_mk(len(chunks), "text", f"таблица {ti}", "\n".join(rows)))

    # Сноски и комментарии лежат в отдельных частях пакета и обычным обходом
    # параграфов не видны — именно поэтому их регулярно теряют.
    for part_name, kind, label in (("footnotes", "footnote", "сноска"),
                                   ("endnotes", "footnote", "концевая сноска"),
                                   ("comments", "comment", "комментарий")):
        try:
            part = doc.part.package.part_related_by(
                f"http://schemas.openxmlformats.org/officeDocument/2006/relationships/{part_name}")
            texts = re.findall(r"<w:t[^>]*>([^<]+)</w:t>", part.blob.decode("utf-8", "ignore"))
            if texts:
                chunks.append(_mk(len(chunks), kind, f"{label}и", "\n".join(texts)))
        except (KeyError, AttributeError, ValueError):
            pass
    return chunks, {"paragraphs": len(doc.paragraphs), "tables": len(doc.tables)}


def _parse_xlsx(path: Path) -> tuple[list[Chunk], dict]:
    import openpyxl

    # Два прохода: значения и формулы. Для финмодели важны оба.
    wb_val = openpyxl.load_workbook(str(path), data_only=True)
    wb_frm = openpyxl.load_workbook(str(path), data_only=False)
    chunks, hidden = [], []

    for name in wb_val.sheetnames:
        ws, wf = wb_val[name], wb_frm[name]
        is_hidden = ws.sheet_state != "visible"
        if is_hidden:
            hidden.append(name)
        rows = []
        for row in ws.iter_rows(values_only=True):
            if any(v is not None for v in row):
                rows.append(" | ".join("" if v is None else str(v) for v in row))
        if rows:
            kind = "hidden" if is_hidden else "sheet"
            label = f"лист '{name}'" + (" (СКРЫТЫЙ)" if is_hidden else "")
            chunks.append(_mk(len(chunks), kind, label, "\n".join(rows)))

        formulas = [f"{c.coordinate}: {c.value}"
                    for row in wf.iter_rows() for c in row
                    if isinstance(c.value, str) and c.value.startswith("=")]
        if formulas:
            chunks.append(_mk(len(chunks), "formula", f"формулы листа '{name}'",
                              "\n".join(formulas)))

    meta = {"sheets": wb_val.sheetnames, "hidden_sheets": hidden}
    if hidden:
        meta["warning"] = f"есть скрытые листы: {', '.join(hidden)} — они включены в разбор"
    return chunks, meta


def _parse_pptx(path: Path) -> tuple[list[Chunk], dict]:
    from pptx import Presentation

    prs = Presentation(str(path))
    chunks = []
    for i, slide in enumerate(prs.slides, 1):
        body = "\n".join(s.text for s in slide.shapes
                         if getattr(s, "has_text_frame", False) and s.text.strip())
        if body:
            chunks.append(_mk(len(chunks), "slide", f"слайд {i}", body))
        # Заметки докладчика нередко содержат цифры, которых нет на слайде.
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text
            if notes.strip():
                chunks.append(_mk(len(chunks), "notes", f"слайд {i} (заметки)", notes))
    return chunks, {"slides": len(prs.slides)}


def _parse_text(path: Path) -> tuple[list[Chunk], dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = [_mk(i, "text", f"фрагмент {i + 1}", p) for i, p in enumerate(paras)]
    return chunks, {"chars": len(text)}


PARSERS = {
    ".pdf": _parse_pdf, ".docx": _parse_docx, ".xlsx": _parse_xlsx, ".xlsm": _parse_xlsx,
    ".pptx": _parse_pptx, ".txt": _parse_text, ".md": _parse_text, ".csv": _parse_text,
}


# ---------------------------------------------------------------- кэш

def load(conn, path: str | Path) -> Document:
    """Разбирает файл или отдаёт готовый разбор из кэша."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    digest = file_hash(path)

    row = conn.execute(
        "SELECT * FROM files WHERE hash = ? AND parser_ver = ?", (digest, PARSER_VERSION)
    ).fetchone()
    if row:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE hash = ? ORDER BY idx", (digest,)).fetchall()
        chunks = [Chunk(r["idx"], r["kind"], r["locator"], r["text"], bool(r["critical"]))
                  for r in rows]
        return Document(digest, row["path"], row["kind"], chunks, json.loads(row["meta"]))

    suffix = path.suffix.lower()
    parser = PARSERS.get(suffix)
    if parser is None:
        raise ValueError(f"нет парсера для {suffix}")
    chunks, meta = parser(path)

    conn.execute(
        "INSERT OR REPLACE INTO files (hash, path, kind, size, parsed_at, parser_ver, meta)"
        " VALUES (?,?,?,?,?,?,?)",
        (digest, str(path), suffix, path.stat().st_size, time.time(), PARSER_VERSION,
         json.dumps(meta, ensure_ascii=False)),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO chunks (hash, idx, kind, locator, critical, text)"
        " VALUES (?,?,?,?,?,?)",
        [(digest, c.idx, c.kind, c.locator, int(c.critical), c.text) for c in chunks],
    )
    conn.commit()
    return Document(digest, str(path), suffix, chunks, meta)


# ---------------------------------------------------------------- подача

def _score(chunk: Chunk, terms: set[str]) -> float:
    """Лексический скоринг. Векторный поиск систематически промахивается по
    номерам пунктов, артикулам и датам, а это ровно то, что ищут чаще всего."""
    low = chunk.text.lower()
    hits = sum(low.count(t) for t in terms)
    if not hits:
        return 0.0
    return hits / (1 + len(low) / 2000) + (0.5 if chunk.critical else 0.0)


def render(doc: Document, level: str, query: str = "", budget_chars: int = 60_000) -> str:
    """level: index | targeted | full."""
    header = f"### Файл: {Path(doc.path).name}\n"
    if warning := doc.meta.get("warning"):
        header += f"ВНИМАНИЕ: {warning}\n"

    if level == "index":
        return header + "Оглавление (полный текст доступен по запросу):\n" + doc.outline

    if level == "full":
        body = "\n\n".join(f"[{c.locator}]\n{c.text}" for c in doc.chunks)
        return header + body

    # targeted: релевантное + соседи + всегда «теряемые» слои целиком
    terms = {t for t in re.findall(r"\w{4,}", query.lower())}
    scored = sorted(doc.chunks, key=lambda c: _score(c, terms), reverse=True)
    keep = {c.idx for c in scored[:8] if _score(c, terms) > 0}
    for idx in list(keep):                       # окрестности попаданий
        keep.update({idx - 1, idx + 1})
    keep.update(c.idx for c in doc.chunks if c.kind in ALWAYS_INCLUDE_KINDS)
    keep.update(c.idx for c in doc.chunks if c.critical and _score(c, terms) > 0)

    selected = [c for c in doc.chunks if c.idx in keep]
    if not selected:
        return render(doc, "full", query, budget_chars)

    total = sum(len(c.text) for c in selected)
    # Отобрали почти весь документ или не влезаем в бюджет — дешевле и безопаснее
    # отдать целиком, чем резать и гадать, что потерялось.
    if total > budget_chars or len(selected) > len(doc.chunks) * 0.7:
        return render(doc, "full", query, budget_chars)

    body = "\n\n".join(f"[{c.locator}]\n{c.text}" for c in selected)
    skipped = len(doc.chunks) - len(selected)
    note = f"\n\n(подано {len(selected)} из {len(doc.chunks)} фрагментов; " \
           f"{skipped} не показано — запросите полный текст, если их содержимое важно)"
    return header + body + note
