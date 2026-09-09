"""位图文字编辑：OCR 定位 -> 像素分析 -> 擦除 -> 按原字体字号重绘。

扫描页/截图这类只有像素、没有文字对象的页面走这条路径；
数字生成的矢量页仍然走 pdf_engine 的原生 redaction + insert，两者不混用。
"""
from .pipeline import apply_edit, apply_edits, inspect_box  # noqa: F401
from . import analyze, fonts, inpaint, ocr, pages, render  # noqa: F401
from .pages import RASTER_DPI, edit_page, ocr_page, page_kind  # noqa: F401
