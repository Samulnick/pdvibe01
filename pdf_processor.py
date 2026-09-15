"""
pdf_processor.py
-----------------
Orchestrates file I/O and page-matrix transfer for pdvibe.

Design goals driven by the spec:
    * Never rasterize the whole page to a single flattened bitmap. Pages
      are transplanted with `insert_pdf` (PyMuPDF) / page copy (pikepdf),
      which preserves vector paths, embedded fonts as real text objects,
      and any raster XObjects at their native resolution and color space.
    * Never force a color-space conversion. We read each source page's
      declared color space (DeviceCMYK / DeviceRGB / ICC-based) and leave
      it untouched; PyMuPDF's `show_pdf_page` / page.insert_pdf preserve
      content streams verbatim, so no conversion happens unless the
      source itself is already mixed.
    * Output structured for CorelDRAW: no flattening, no annotation-only
      placeholders, real /Contents streams, real fonts.

This module intentionally does the "dumb" mechanical work only â€” all
page-order and rotation decisions come from imposition_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - surfaced to the UI instead of crashing import
    fitz = None

from imposition_engine import (
    ImpositionType,
    ImposedSheet,
    CellRect,
    compute_grid_geometry,
    fit_scale,
    expand_book,
)


@dataclass
class SourceDocInfo:
    path: str
    page_count: int
    page_size_pt: Tuple[float, float]   # first-page (w, h) in points, for reference only
    color_space_hint: str                # 'CMYK' / 'RGB' / 'Mixed' / 'Unknown'


def inspect_source(path: str) -> SourceDocInfo:
    """Open the source PDF read-only just to report page count / size /
    color-space hint back to the UI. Does not modify anything."""
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed.")

    doc = fitz.open(path)
    try:
        page_count = doc.page_count
        first = doc.load_page(0) if page_count else None
        size = (first.rect.width, first.rect.height) if first else (0.0, 0.0)

        cs_hint = "Unknown"
        if first is not None:
            cs_names = set()
            for img in first.get_images(full=True):
                xref = img[0]
                cs = doc.xref_get_key(xref, "ColorSpace")
                if cs and cs[1]:
                    if "CMYK" in cs[1]:
                        cs_names.add("CMYK")
                    elif "RGB" in cs[1]:
                        cs_names.add("RGB")
            if len(cs_names) == 1:
                cs_hint = cs_names.pop()
            elif len(cs_names) > 1:
                cs_hint = "Mixed"
        return SourceDocInfo(path=path, page_count=page_count,
                              page_size_pt=size, color_space_hint=cs_hint)
    finally:
        doc.close()


def build_imposed_pdf(source_path: str, output_path: str,
                       imposition_type: ImpositionType,
                       signature_label: str,
                       sheet_width_pt: float, sheet_height_pt: float,
                       margin_pt: float = 0.0, gutter_pt: float = 0.0,
                       paper_thickness_mm: float = 0.0) -> str:
    """
    Read `source_path`, impose it according to the chosen preset, and
    write the result to `output_path`. Returns output_path on success.

    Each imposed broadsheet becomes one page of the output front, followed
    by one page for the back (front/back alternate: sheet1-front,
    sheet1-back, sheet2-front, sheet2-back, ...), which is the layout
    CorelDRAW / most digital front-to-back presses expect for duplex jobs.
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed.")

    info = inspect_source(source_path)
    sheets: List[ImposedSheet] = expand_book(
        total_input_pages=info.page_count,
        imposition_type=imposition_type,
        signature_label=signature_label,
        paper_thickness_mm=paper_thickness_mm,
    )

    src = fitz.open(source_path)
    out = fitz.open()

    try:
        for sheet in sheets:
            _write_side(out, src, sheet, side="front",
                        sheet_w=sheet_width_pt, sheet_h=sheet_height_pt,
                        margin=margin_pt, gutter=gutter_pt)
            _write_side(out, src, sheet, side="back",
                        sheet_w=sheet_width_pt, sheet_h=sheet_height_pt,
                        margin=margin_pt, gutter=gutter_pt)

        # Preserve/declare an output intent-friendly, non-lossy save:
        # no garbage collection that could merge/flatten streams away.
        out.save(output_path, deflate=True, clean=False)
        return output_path
    finally:
        out.close()
        src.close()


def _write_side(out_doc: "fitz.Document", src_doc: "fitz.Document",
                 sheet: ImposedSheet, side: str,
                 sheet_w: float, sheet_h: float,
                 margin: float, gutter: float) -> None:
    layout = sheet.layout
    side_template = layout.front if side == "front" else layout.back
    page_numbers = sheet.front_pages if side == "front" else sheet.back_pages

    out_page = out_doc.new_page(width=sheet_w, height=sheet_h)
    cells: List[CellRect] = compute_grid_geometry(
        sheet_w, sheet_h, side_template.rows, side_template.cols,
        margin=margin, gutter=gutter,
    )

    # side_template.slots are stored in the same row-major flattening used
    # by _offset_slots() in imposition_engine, so zip 1:1 with cells.
    for cell, page_no, slot in zip(cells, page_numbers, side_template.slots):
        if page_no is None:
            continue  # virtual blank: leave the cell empty
        src_page = src_doc.load_page(page_no - 1)  # 1-indexed -> 0-indexed
        scale = fit_scale(src_page.rect.width, src_page.rect.height,
                           cell.width, cell.height)
        target_w = src_page.rect.width * scale
        target_h = src_page.rect.height * scale
        # center within the cell
        dx = cell.x + (cell.width - target_w) / 2
        dy = cell.y + (cell.height - target_h) / 2
        target_rect = fitz.Rect(dx, dy, dx + target_w, dy + target_h)

        # show_pdf_page transplants the page's content stream (vectors,
        # text, embedded raster) without rasterizing or converting color.
        out_page.show_pdf_page(target_rect, src_doc, page_no - 1,
                                rotate=slot.rotation)
