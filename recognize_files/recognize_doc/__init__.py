from recognize_files.recognize_doc.binary_stub import recognize_binary_stub
from recognize_files.recognize_doc.doc_file import recognize_doc
from recognize_files.recognize_doc.docx_file import recognize_docx
from recognize_files.recognize_doc.image_ocr import recognize_image_ocr
from recognize_files.recognize_doc.pdf_file import recognize_pdf
from recognize_files.recognize_doc.plain_text import recognize_plain_text
from recognize_files.recognize_doc.xls_file import recognize_xls
from recognize_files.recognize_doc.xlsx_file import recognize_xlsx

__all__ = [
    "recognize_pdf",
    "recognize_doc",
    "recognize_docx",
    "recognize_xls",
    "recognize_xlsx",
    "recognize_plain_text",
    "recognize_image_ocr",
    "recognize_binary_stub",
]
