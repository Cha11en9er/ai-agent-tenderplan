from __future__ import annotations

from pathlib import Path


def _norm(s: str) -> str:
    return " ".join((s or "").replace("\n", " ").split())


def _cell_text(cell) -> str:
    # В одной ячейке может быть несколько абзацев.
    parts = [_norm(p.text) for p in cell.paragraphs if _norm(p.text)]
    return " | ".join(parts).strip()


def _table_to_markdown(table, table_idx: int) -> list[str]:
    rows: list[list[str]] = []
    for row in table.rows:
        rows.append([_cell_text(cell) for cell in row.cells])

    if not rows:
        return [f"[TABLE {table_idx}] (empty)", ""]

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    out: list[str] = [f"[TABLE {table_idx}]"]
    header = rows[0]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join(["---"] * width) + " |")
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    out.append("")
    return out


def recognize_docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    lines: list[str] = [f"[DOCX] {path.name}", ""]

    # Важно: сохраняем порядок блоков как в документе (paragraph/table/paragraph...).
    p_idx = 0
    t_idx = 0
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            if p_idx >= len(document.paragraphs):
                continue
            text = _norm(document.paragraphs[p_idx].text)
            p_idx += 1
            if text:
                lines.append(text)
                lines.append("")
        elif tag == "tbl":
            if t_idx >= len(document.tables):
                continue
            t_idx += 1
            lines.extend(_table_to_markdown(document.tables[t_idx - 1], t_idx))

    return "\n".join(lines).strip()
