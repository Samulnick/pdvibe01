"""
imposition_engine.py
---------------------
Pure-math core of pdvibe. No I/O, no PDF libraries, no UI imports here —
this module is deliberately kept side-effect free so it can be unit tested
in isolation and reused by both the GUI (ui_main.py) and the PDF writer
(pdf_processor.py).

Responsibilities:
    * Define the exact signature layout schemas (page routing + rotation)
      for every entry in the "Imposition Type" / "Signature Size" dropdown
      pair.
    * Expand a layout preset into a full per-sheet page map for an
      arbitrary total page count, including auto-padding with virtual
      blank pages.
    * Compute creep (shingling) compensation offsets.
    * Compute sheet/grid geometry (cell rects in points) for a given
      target sheet size.
    * Render a human-readable "Prepress Schema Mapping Preview" string for
      the read-only QTextEdit in the UI.

Terminology
-----------
"Signature" = the group of pages that come from a single gathered set of
folded sheets (16-page signature = 4 sheets nested inside one another,
8-page = 2 sheets, 4-page = 1 sheet).

"N" (per the spec) = the structural page-number offset of a given
signature block within the whole book. Signature 0 covers pages
1..signature_size, signature 1 covers pages (signature_size+1)..
(2*signature_size), etc. For "2-Up Multi-Signature" mode, two signature
blocks (a "left" block and a "right", mirrored, block) are gathered onto
the same physical broadsheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple, Dict


# --------------------------------------------------------------------------- #
# Constants / enums
# --------------------------------------------------------------------------- #

class ImpositionType(str, Enum):
    STANDARD_SADDLE_STITCH = "Standard Saddle-Stitch"
    TWO_UP_MULTI_SIGNATURE = "2-Up Multi-Signature Saddle-Stitch"


# Canonical dropdown-2 labels, keyed by which imposition type allows them.
SIGNATURE_SIZE_OPTIONS: Dict[ImpositionType, List[str]] = {
    ImpositionType.STANDARD_SADDLE_STITCH: [
        "16-Page Signature (2x4 Grid)",
        "8-Page Signature (2x2 Grid)",
        "4-Page Signature (1x2 Grid)",
    ],
    ImpositionType.TWO_UP_MULTI_SIGNATURE: [
        "8-Page Signature (2x2 Grid)",
        "4-Page Signature (1x2 Grid)",
    ],
}

ROTATE_180 = 180
ROTATE_0 = 0


@dataclass(frozen=True)
class Slot:
    """One imposed page position on a broadsheet."""
    page: Optional[int]        # None => virtual blank
    rotation: int              # 0 or 180 (degrees, about the page's own center)
    row: int                   # 0-indexed grid row
    col: int                   # 0-indexed grid col

    def is_blank(self) -> bool:
        return self.page is None


@dataclass(frozen=True)
class SheetSide:
    """One printed side (front or back) of a broadsheet: a grid of Slots."""
    rows: int
    cols: int
    slots: Tuple[Slot, ...]

    def grid(self) -> List[List[Slot]]:
        """Return slots arranged as [row][col]."""
        out = [[None] * self.cols for _ in range(self.rows)]  # type: ignore
        for s in self.slots:
            out[s.row][s.col] = s
        return out


@dataclass(frozen=True)
class SheetLayout:
    """A full broadsheet: front + back sides, plus how many source pages
    (the 'signature size') one instance of this layout consumes."""
    front: SheetSide
    back: SheetSide
    pages_per_instance: int   # e.g. 16, 8, or 4
    grid_rows: int
    grid_cols: int


# --------------------------------------------------------------------------- #
# 1. Hard-specified base patterns (verbatim from the prepress spec)
#
#    These two are given exactly by the client spec and are NOT derived —
#    they are transcribed as literal routing tables to guarantee an exact
#    match to what the pressroom expects.
# --------------------------------------------------------------------------- #

def _slots_from_rows(top_row: List[int], bottom_row: List[int]) -> Tuple[Slot, ...]:
    """Helper: build Slot tuples for a 2-row grid where the top row is
    rotated 180 degrees and the bottom row is unrotated, per spec section 3."""
    slots: List[Slot] = []
    for col, page in enumerate(top_row):
        slots.append(Slot(page=page, rotation=ROTATE_180, row=0, col=col))
    for col, page in enumerate(bottom_row):
        slots.append(Slot(page=page, rotation=ROTATE_0, row=1, col=col))
    return tuple(slots)


# --- 16-Page Signature (2x4 Grid) - Standard Saddle-Stitch (spec 3.A) ------- #
_STD_16_FRONT = _slots_from_rows(
    top_row=[12, 5, 8, 9],
    bottom_row=[4, 13, 16, 1],
)
_STD_16_BACK = _slots_from_rows(
    top_row=[10, 7, 6, 11],
    bottom_row=[2, 15, 14, 3],
)
STANDARD_16PG_LAYOUT = SheetLayout(
    front=SheetSide(rows=2, cols=4, slots=_STD_16_FRONT),
    back=SheetSide(rows=2, cols=4, slots=_STD_16_BACK),
    pages_per_instance=16,
    grid_rows=2,
    grid_cols=4,
)

# --- 8-Page Signature (2x2 Grid) - 2-Up Multi-Signature (spec 3.B) --------- #
# This pattern gathers TWO 4-page signature blocks side by side on one
# broadsheet: a "left" block (N=0, pages 1-4) and a "right" block
# (N=28, pages 29-32, i.e. some later signature 7 books further in).
# The right block's front/back top/bottom pairing is mirrored relative to
# the left block, which is standard practice so that the two signatures
# separate cleanly with correct page order after guillotine cutting.
_TWOUP_8_FRONT = _slots_from_rows(
    top_row=[4, 29],
    bottom_row=[32, 1],
)
_TWOUP_8_BACK = _slots_from_rows(
    top_row=[30, 3],
    bottom_row=[2, 31],
)
TWO_UP_8PG_LAYOUT = SheetLayout(
    front=SheetSide(rows=2, cols=2, slots=_TWOUP_8_FRONT),
    back=SheetSide(rows=2, cols=2, slots=_TWOUP_8_BACK),
    pages_per_instance=8,
    grid_rows=2,
    grid_cols=2,
)


# --------------------------------------------------------------------------- #
# 2. Generic base blocks (derived, standard prepress convention)
#
#    The spec's two dropdown menus also expose a standalone 8-page signature
#    (Standard mode) and a 4-page signature (both modes) without giving
#    literal numbers for them. Those are generated here from the same
#    "N-block" building block that the 2-Up 8-page preset above is built
#    from, so behaviour is consistent across all signature sizes:
#
#      BASE-4  (1 sheet, 1x2 grid, no rotation needed - single fold):
#          front: [page N+4, page N+1]      (outer spread)
#          back:  [page N+2, page N+3]      (inner spread)
#
#      MIRROR-4 (used for the "right" block of a 2-up sheet):
#          front: [page N+1, page N+4]
#          back:  [page N+3, page N+2]
#
#    BASE-8 (2 sheets nested -> 2x2 grid) is BASE-4 with an outer sheet
#    wrapped around it, matching the exact 16pg pattern's structure scaled
#    down by one nesting level.
# --------------------------------------------------------------------------- #

def _base_block_4(n: int, mirror: bool = False) -> Dict[str, List[int]]:
    """Return {'front': [a, b], 'back': [c, d]} for a single 4-page
    signature block whose pages are N+1..N+4."""
    a, b, c, d = n + 4, n + 1, n + 2, n + 3
    if not mirror:
        return {"front": [a, b], "back": [c, d]}
    # mirrored block: swap left/right within each side (used as the
    # "right-hand" signature of a 2-up sheet, matching TWO_UP_8PG_LAYOUT)
    return {"front": [b, a], "back": [d, c]}


def generate_4pg_layout(n_offset: int = 0) -> SheetLayout:
    """Standalone 4-Page Signature (1x2 Grid). Single fold => no rotation
    row is required (both slots sit at 0 degrees, side by side)."""
    block = _base_block_4(n_offset, mirror=False)
    front = (
        Slot(page=block["front"][0], rotation=ROTATE_0, row=0, col=0),
        Slot(page=block["front"][1], rotation=ROTATE_0, row=0, col=1),
    )
    back = (
        Slot(page=block["back"][0], rotation=ROTATE_0, row=0, col=0),
        Slot(page=block["back"][1], rotation=ROTATE_0, row=0, col=1),
    )
    return SheetLayout(
        front=SheetSide(rows=1, cols=2, slots=front),
        back=SheetSide(rows=1, cols=2, slots=back),
        pages_per_instance=4,
        grid_rows=1,
        grid_cols=2,
    )


def generate_2up_4pg_layout(n_left: int = 0, n_right: int = 28) -> SheetLayout:
    """2-Up variant of the 4-page signature: two BASE-4 blocks side by
    side (left = n_left offset, right = mirrored n_right offset),
    consistent with the TWO_UP_8PG_LAYOUT construction above."""
    left = _base_block_4(n_left, mirror=False)
    right = _base_block_4(n_right, mirror=True)
    front = (
        Slot(page=left["front"][0], rotation=ROTATE_0, row=0, col=0),
        Slot(page=right["front"][0], rotation=ROTATE_0, row=0, col=1),
    )
    back = (
        Slot(page=left["back"][0], rotation=ROTATE_0, row=0, col=0),
        Slot(page=right["back"][0], rotation=ROTATE_0, row=0, col=1),
    )
    return SheetLayout(
        front=SheetSide(rows=1, cols=2, slots=front),
        back=SheetSide(rows=1, cols=2, slots=back),
        pages_per_instance=8,   # consumes 2 x 4pg blocks per broadsheet
        grid_rows=1,
        grid_cols=2,
    )


def generate_8pg_layout(n_offset: int = 0) -> SheetLayout:
    """Standalone 8-Page Signature (2x2 Grid), Standard mode. Built as an
    outer sheet wrapped around a BASE-4 inner block, following the same
    nesting principle demonstrated by STANDARD_16PG_LAYOUT (outer pages on
    the rotated row, inner pages on the unrotated row)."""
    # inner sheet (BASE-4, pages N+1..N+4) and outer wrap (pages N+5..N+8)
    inner = _base_block_4(n_offset, mirror=False)
    o = n_offset
    outer_front = [o + 8, o + 5]   # outer wrap, mirrors inner front order
    outer_back = [o + 6, o + 7]
    top_row = [outer_front[0], inner["front"][0]]
    bottom_row = [inner["front"][1], outer_front[1]]
    top_row_b = [outer_back[0], inner["back"][0]]
    bottom_row_b = [inner["back"][1], outer_back[1]]
    front = _slots_from_rows(top_row, bottom_row)
    back = _slots_from_rows(top_row_b, bottom_row_b)
    return SheetLayout(
        front=SheetSide(rows=2, cols=2, slots=front),
        back=SheetSide(rows=2, cols=2, slots=back),
        pages_per_instance=8,
        grid_rows=2,
        grid_cols=2,
    )


# --------------------------------------------------------------------------- #
# 3. Preset registry -> what the two cascading dropdowns actually resolve to
# --------------------------------------------------------------------------- #

def resolve_layout(imposition_type: ImpositionType, signature_label: str) -> SheetLayout:
    """Map (Dropdown1, Dropdown2) selections to a concrete SheetLayout
    template (page numbers are relative to N=0; use expand_book() to
    substitute real page numbers across an entire document)."""
    key = (imposition_type, signature_label)

    table = {
        (ImpositionType.STANDARD_SADDLE_STITCH, "16-Page Signature (2x4 Grid)"): STANDARD_16PG_LAYOUT,
        (ImpositionType.STANDARD_SADDLE_STITCH, "8-Page Signature (2x2 Grid)"): generate_8pg_layout(0),
        (ImpositionType.STANDARD_SADDLE_STITCH, "4-Page Signature (1x2 Grid)"): generate_4pg_layout(0),
        (ImpositionType.TWO_UP_MULTI_SIGNATURE, "8-Page Signature (2x2 Grid)"): TWO_UP_8PG_LAYOUT,
        (ImpositionType.TWO_UP_MULTI_SIGNATURE, "4-Page Signature (1x2 Grid)"): generate_2up_4pg_layout(0, 28),
    }
    if key not in table:
        raise ValueError(f"No layout registered for {imposition_type!r} / {signature_label!r}")
    return table[key]


# --------------------------------------------------------------------------- #
# 4. Auto-padding
# --------------------------------------------------------------------------- #

def compute_padding(total_input_pages: int, signature_size: int) -> int:
    """Return the number of virtual blank pages that must be appended so
    total_input_pages is an exact multiple of signature_size."""
    if signature_size <= 0:
        raise ValueError("signature_size must be positive")
    remainder = total_input_pages % signature_size
    return 0 if remainder == 0 else (signature_size - remainder)


# --------------------------------------------------------------------------- #
# 5. Creep (shingling) compensation
# --------------------------------------------------------------------------- #

def compute_creep_shift(current_sheet_index: int, total_sheets_in_signature: int,
                         paper_thickness_mm: float) -> float:
    """
    shift = (total_sheets_in_signature - current_sheet_index) * thickness

    current_sheet_index is 1-indexed, counted from the OUTERMOST sheet of
    the signature (sheet 1). The outermost sheet gets the largest push-in;
    the innermost sheet (current_sheet_index == total_sheets_in_signature)
    gets zero shift.
    """
    if paper_thickness_mm < 0:
        raise ValueError("paper_thickness_mm cannot be negative")
    if not (1 <= current_sheet_index <= total_sheets_in_signature):
        raise ValueError("current_sheet_index out of range for this signature")
    return (total_sheets_in_signature - current_sheet_index) * paper_thickness_mm


# --------------------------------------------------------------------------- #
# 6. Sheet/grid geometry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CellRect:
    x: float
    y: float
    width: float
    height: float
    row: int
    col: int


def compute_grid_geometry(sheet_width: float, sheet_height: float,
                           rows: int, cols: int, margin: float = 0.0,
                           gutter: float = 0.0) -> List[CellRect]:
    """
    Compute cell rectangles (in the same units as sheet_width/height, e.g.
    points) for a rows x cols grid on a broadsheet of the given size.

    margin  - outer margin applied to all four edges of the sheet
    gutter  - spacing between adjacent grid cells
    """
    if rows <= 0 or cols <= 0:
        raise ValueError("rows and cols must be positive")

    usable_w = sheet_width - 2 * margin - gutter * (cols - 1)
    usable_h = sheet_height - 2 * margin - gutter * (rows - 1)
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("Margins/gutters leave no usable area on this sheet size")

    cell_w = usable_w / cols
    cell_h = usable_h / rows

    rects: List[CellRect] = []
    for row in range(rows):
        for col in range(cols):
            x = margin + col * (cell_w + gutter)
            # row 0 is the TOP row -> highest y in a top-left origin system
            y = margin + row * (cell_h + gutter)
            rects.append(CellRect(x=x, y=y, width=cell_w, height=cell_h, row=row, col=col))
    return rects


def fit_scale(source_w: float, source_h: float, cell_w: float, cell_h: float) -> float:
    """Uniform scale factor to fit a source page into a grid cell without
    distortion (used before placing/centering the page in the cell)."""
    return min(cell_w / source_w, cell_h / source_h)


# --------------------------------------------------------------------------- #
# 7. Full-book expansion: map every physical sheet across the whole document
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ImposedSheet:
    sheet_index: int              # 1-indexed, outermost = 1, within its signature
    signature_index: int          # which signature block (0-indexed) this sheet belongs to
    layout: SheetLayout
    front_pages: List[Optional[int]]   # None = blank, flattened row-major
    back_pages: List[Optional[int]]
    creep_shift_mm: float


def _offset_slots(side: SheetSide, n_offset: int, total_pages_with_padding: int) -> List[Optional[int]]:
    """Apply the N-offset to a template SheetSide and null out any page
    numbers that fall beyond the (padded) document length."""
    out: List[Optional[int]] = []
    for s in side.slots:
        if s.page is None:
            out.append(None)
            continue
        pg = s.page + n_offset
        out.append(pg if pg <= total_pages_with_padding else None)
    return out


def expand_book(total_input_pages: int, imposition_type: ImpositionType,
                 signature_label: str, paper_thickness_mm: float = 0.0) -> List[ImposedSheet]:
    """
    Build the complete, ordered list of physical broadsheets needed to
    impose the whole document, including padding and creep.
    """
    layout = resolve_layout(imposition_type, signature_label)
    sig_size = layout.pages_per_instance
    padding = compute_padding(total_input_pages, sig_size)
    total_padded = total_input_pages + padding

    n_signatures = total_padded // sig_size
    sheets: List[ImposedSheet] = []

    for sig_idx in range(n_signatures):
        n_offset = sig_idx * sig_size
        front_pages = _offset_slots(layout.front, n_offset, total_padded)
        back_pages = _offset_slots(layout.back, n_offset, total_padded)
        creep = compute_creep_shift(
            current_sheet_index=1,
            total_sheets_in_signature=max(1, n_signatures),
            paper_thickness_mm=paper_thickness_mm,
        ) if paper_thickness_mm else 0.0
        sheets.append(ImposedSheet(
            sheet_index=sig_idx + 1,
            signature_index=sig_idx,
            layout=layout,
            front_pages=front_pages,
            back_pages=back_pages,
            creep_shift_mm=creep,
        ))
    return sheets


# --------------------------------------------------------------------------- #
# 8. Human-readable mapping preview (feeds the QTextEdit in ui_main.py)
# --------------------------------------------------------------------------- #

def format_mapping_preview(imposition_type: ImpositionType, signature_label: str,
                            total_input_pages: Optional[int] = None) -> str:
    layout = resolve_layout(imposition_type, signature_label)
    lines: List[str] = []
    lines.append(f"Imposition Type : {imposition_type.value}")
    lines.append(f"Signature       : {signature_label}")
    lines.append(f"Grid            : {layout.grid_rows} rows x {layout.grid_cols} cols per side")
    lines.append("")

    def render_side(name: str, side: SheetSide) -> None:
        lines.append(f"{name} side:")
        grid = side.grid()
        for row in grid:
            cells = []
            for slot in row:
                if slot is None:
                    cells.append("  .  ")
                else:
                    tag = f"P{slot.page}"
                    if slot.rotation:
                        tag += f"({slot.rotation}\u00b0)"
                    cells.append(f"{tag:>7}")
            lines.append("  [ " + " | ".join(cells) + " ]")
        lines.append("")

    render_side("FRONT", layout.front)
    render_side("BACK", layout.back)

    if total_input_pages is not None:
        padding = compute_padding(total_input_pages, layout.pages_per_instance)
        total_padded = total_input_pages + padding
        n_sheets = total_padded // layout.pages_per_instance
        lines.append(f"Input pages     : {total_input_pages}")
        lines.append(f"Blank padding   : {padding}")
        lines.append(f"Total sheets    : {n_sheets}")

    return "\n".join(lines)
