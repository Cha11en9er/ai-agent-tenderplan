from __future__ import annotations

from pathlib import Path


def recognize_xls(path: Path) -> str:
    import xlrd

    wb = xlrd.open_workbook(str(path), formatting_info=False)
    lines: list[str] = []
    for i in range(wb.nsheets):
        sheet = wb.sheet_by_index(i)
        lines.append(f"## Лист: {sheet.name}")
        for r in range(sheet.nrows):
            vals: list[str] = []
            for c in range(sheet.ncols):
                v = sheet.cell_value(r, c)
                vals.append("" if v is None else str(v))
            lines.append("\t".join(vals))
        lines.append("")
    return "\n".join(lines).strip()

