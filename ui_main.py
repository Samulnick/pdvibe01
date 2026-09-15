"""
ui_main.py
----------
pdvibe main window: PyQt6, flat dark theme, cascading imposition dropdowns,
a live schematic print-preview canvas, and a read-only prepress mapping
console. All layout math is delegated to imposition_engine.py; all file
I/O is delegated to pdf_processor.py.
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QSizeF
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QTransform, QPainter
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QComboBox, QPushButton, QTextEdit, QLineEdit, QDoubleSpinBox,
    QGroupBox, QRadioButton, QButtonGroup, QFileDialog, QMessageBox,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsSimpleTextItem,
    QTabWidget, QSplitter, QFrame,
)

import imposition_engine as eng
from imposition_engine import ImpositionType

try:
    import pdf_processor
except ImportError:
    pdf_processor = None


# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #

SURFACE = "#3c3f41"
FIELD = "#2b2b2b"
TEXT = "#ffffff"
ACCENT = "#5c9dff"
BORDER = "#555a5e"
MUTED = "#9aa0a6"

DARK_STYLESHEET = f"""
QWidget {{
    background-color: {SURFACE};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}
QMainWindow {{
    background-color: {SURFACE};
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {MUTED};
}}
QComboBox, QLineEdit, QDoubleSpinBox {{
    background-color: {FIELD};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 8px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {FIELD};
    color: {TEXT};
    selection-background-color: {ACCENT};
    border: 1px solid {BORDER};
    outline: none;
}}
QTextEdit {{
    background-color: {FIELD};
    border: 1px solid {BORDER};
    border-radius: 4px;
    color: #d6d6d6;
    font-family: "Consolas", "Menlo", monospace;
    font-size: 12px;
}}
QPushButton {{
    background-color: {ACCENT};
    color: #0d1117;
    border: none;
    border-radius: 5px;
    padding: 8px 16px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: #7db0ff;
}}
QPushButton:disabled {{
    background-color: #4a4d4f;
    color: {MUTED};
}}
QPushButton#secondary {{
    background-color: {FIELD};
    color: {TEXT};
    border: 1px solid {BORDER};
}}
QPushButton#secondary:hover {{
    background-color: #33373a;
}}
QRadioButton {{
    spacing: 6px;
}}
QLabel[role="section"] {{
    color: {MUTED};
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    font-size: 11px;
}}
QGraphicsView {{
    background-color: #232526;
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QTabBar::tab {{
    background: {FIELD};
    color: {MUTED};
    padding: 6px 14px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{
    background: {ACCENT};
    color: #0d1117;
}}
QSplitter::handle {{
    background-color: {BORDER};
}}
"""

A_SERIES_MM = {
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
}

MM_TO_PT = 72.0 / 25.4
IN_TO_PT = 72.0


# --------------------------------------------------------------------------- #
# Live preview canvas
# --------------------------------------------------------------------------- #

class SignaturePreviewView(QGraphicsView):
    """Schematic (non-photographic) front/back signature preview: draws
    the grid, page numbers, and rotation indicators for the currently
    selected layout so the operator can sanity-check it before running."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(260)

    def render_layout(self, layout: eng.SheetLayout, side: str = "front") -> None:
        self._scene.clear()
        side_template = layout.front if side == "front" else layout.back
        grid = side_template.grid()

        cell_w, cell_h = 130.0, 170.0
        pad = 8.0
        origin_x, origin_y = 20.0, 20.0

        header = QGraphicsSimpleTextItem(f"{side.upper()} — {side_template.rows} x {side_template.cols}")
        header.setBrush(QBrush(QColor(TEXT)))
        header.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        header.setPos(origin_x, origin_y - 18)
        self._scene.addItem(header)

        for row_idx, row in enumerate(grid):
            for col_idx, slot in enumerate(row):
                x = origin_x + col_idx * (cell_w + pad)
                y = origin_y + row_idx * (cell_h + pad)

                rect_item = QGraphicsRectItem(QRectF(0, 0, cell_w, cell_h))
                rect_item.setPos(x, y)
                if slot is None or slot.is_blank():
                    rect_item.setBrush(QBrush(QColor(FIELD)))
                    rect_item.setPen(QPen(QColor(BORDER), 1, Qt.PenStyle.DashLine))
                else:
                    rect_item.setBrush(QBrush(QColor("#33454f") if slot.rotation == 0 else QColor("#4f3345")))
                    rect_item.setPen(QPen(QColor(ACCENT), 1))
                rect_item.setTransformOriginPoint(cell_w / 2, cell_h / 2)
                self._scene.addItem(rect_item)

                if slot is not None and not slot.is_blank():
                    label = QGraphicsSimpleTextItem(f"P{slot.page}")
                    label.setBrush(QBrush(QColor(TEXT)))
                    label.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
                    label.setPos(x + cell_w / 2 - 14, y + cell_h / 2 - 10)
                    if slot.rotation == 180:
                        label.setTransformOriginPoint(14, 10)
                        label.setRotation(180)
                    self._scene.addItem(label)

                    rot_tag = QGraphicsSimpleTextItem(
                        f"{slot.rotation}\u00b0" if slot.rotation else "0\u00b0"
                    )
                    rot_tag.setBrush(QBrush(QColor(MUTED)))
                    rot_tag.setFont(QFont("Segoe UI", 8))
                    rot_tag.setPos(x + 4, y + cell_h - 16)
                    self._scene.addItem(rot_tag)

        total_w = origin_x * 2 + side_template.cols * (cell_w + pad)
        total_h = origin_y * 2 + side_template.rows * (cell_h + pad) + 20
        self._scene.setSceneRect(0, 0, total_w, total_h)
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):  # keep the schematic fitted on resize
        super().resizeEvent(event)
        if self._scene.sceneRect().isValid():
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("pdvibe — PDF Imposition for Print")
        self.resize(1180, 760)

        self.source_path: Optional[str] = None
        self.source_info = None  # pdf_processor.SourceDocInfo

        self._build_ui()
        self._wire_signals()
        self._on_imposition_type_changed(self.imposition_type_combo.currentIndex())

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        splitter.addWidget(self._build_control_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 760])

    # -- left: controls -------------------------------------------------- #

    def _build_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(12)

        # Source file
        src_box = QGroupBox("Source PDF")
        src_layout = QHBoxLayout(src_box)
        self.source_label = QLineEdit()
        self.source_label.setReadOnly(True)
        self.source_label.setPlaceholderText("No file selected…")
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("secondary")
        browse_btn.clicked.connect(self._on_browse_source)
        src_layout.addWidget(self.source_label)
        src_layout.addWidget(browse_btn)
        layout.addWidget(src_box)

        # Imposition dropdowns
        imp_box = QGroupBox("Imposition")
        imp_form = QFormLayout(imp_box)

        self.imposition_type_combo = QComboBox()
        for t in ImpositionType:
            self.imposition_type_combo.addItem(t.value, t)
        imp_form.addRow("Imposition Type", self.imposition_type_combo)

        self.signature_size_combo = QComboBox()
        imp_form.addRow("Signature Size", self.signature_size_combo)

        self.side_combo = QComboBox()
        self.side_combo.addItems(["Front", "Back"])
        imp_form.addRow("Preview Side", self.side_combo)

        layout.addWidget(imp_box)

        # Sheet geometry
        sheet_box = QGroupBox("Target Sheet")
        sheet_layout = QVBoxLayout(sheet_box)

        preset_row = QHBoxLayout()
        self.sheet_mode_group = QButtonGroup(self)
        self.radio_preset = QRadioButton("ISO A-Series")
        self.radio_custom = QRadioButton("Custom")
        self.radio_preset.setChecked(True)
        self.sheet_mode_group.addButton(self.radio_preset)
        self.sheet_mode_group.addButton(self.radio_custom)
        preset_row.addWidget(self.radio_preset)
        preset_row.addWidget(self.radio_custom)
        sheet_layout.addLayout(preset_row)

        self.a_series_combo = QComboBox()
        self.a_series_combo.addItems(list(A_SERIES_MM.keys()))
        self.a_series_combo.setCurrentText("A2")
        sheet_layout.addWidget(self.a_series_combo)

        custom_form = QFormLayout()
        self.custom_width = QDoubleSpinBox()
        self.custom_width.setRange(1, 5000)
        self.custom_width.setValue(420.0)
        self.custom_height = QDoubleSpinBox()
        self.custom_height.setRange(1, 5000)
        self.custom_height.setValue(594.0)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["mm", "inches"])
        custom_form.addRow("Width", self.custom_width)
        custom_form.addRow("Height", self.custom_height)
        custom_form.addRow("Units", self.unit_combo)
        sheet_layout.addLayout(custom_form)

        layout.addWidget(sheet_box)

        # Creep / margins
        creep_box = QGroupBox("Creep & Margins")
        creep_form = QFormLayout(creep_box)
        self.thickness_spin = QDoubleSpinBox()
        self.thickness_spin.setRange(0.0, 5.0)
        self.thickness_spin.setSingleStep(0.01)
        self.thickness_spin.setDecimals(3)
        self.thickness_spin.setValue(0.1)
        self.thickness_spin.setSuffix(" mm")
        creep_form.addRow("Paper Thickness", self.thickness_spin)

        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0.0, 100.0)
        self.margin_spin.setValue(5.0)
        self.margin_spin.setSuffix(" mm")
        creep_form.addRow("Sheet Margin", self.margin_spin)

        self.gutter_spin = QDoubleSpinBox()
        self.gutter_spin.setRange(0.0, 50.0)
        self.gutter_spin.setValue(0.0)
        self.gutter_spin.setSuffix(" mm")
        creep_form.addRow("Gutter", self.gutter_spin)

        layout.addWidget(creep_box)

        # Mapping preview console
        map_box = QGroupBox("Prepress Schema Mapping Preview")
        map_layout = QVBoxLayout(map_box)
        self.mapping_preview = QTextEdit()
        self.mapping_preview.setReadOnly(True)
        self.mapping_preview.setMinimumHeight(180)
        map_layout.addWidget(self.mapping_preview)
        layout.addWidget(map_box)

        layout.addStretch(1)

        # Run button
        self.run_btn = QPushButton("Impose && Export PDF")
        self.run_btn.clicked.connect(self._on_run_imposition)
        layout.addWidget(self.run_btn)

        return panel

    # -- right: live preview ---------------------------------------------- #

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        title = QLabel("Live Print Preview")
        title.setProperty("role", "section")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.preview_view = SignaturePreviewView()
        self.tabs.addTab(self.preview_view, "Signature Layout")
        layout.addWidget(self.tabs, 1)

        info_frame = QFrame()
        info_layout = QVBoxLayout(info_frame)
        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet(f"color: {MUTED};")
        info_layout.addWidget(self.status_label)
        layout.addWidget(info_frame)

        return panel

    # ------------------------------------------------------------------ #
    # Signal wiring
    # ------------------------------------------------------------------ #

    def _wire_signals(self) -> None:
        self.imposition_type_combo.currentIndexChanged.connect(self._on_imposition_type_changed)
        self.signature_size_combo.currentIndexChanged.connect(self._refresh_preview_and_mapping)
        self.side_combo.currentIndexChanged.connect(self._refresh_preview_and_mapping)
        self.radio_preset.toggled.connect(self._on_sheet_mode_toggled)
        self.a_series_combo.currentIndexChanged.connect(self._refresh_preview_and_mapping)
        self.custom_width.valueChanged.connect(self._refresh_preview_and_mapping)
        self.custom_height.valueChanged.connect(self._refresh_preview_and_mapping)

    # ------------------------------------------------------------------ #
    # Cascading dropdown logic (spec section 2)
    # ------------------------------------------------------------------ #

    def _on_imposition_type_changed(self, _index: int) -> None:
        imp_type: ImpositionType = self.imposition_type_combo.currentData()
        self.signature_size_combo.blockSignals(True)
        self.signature_size_combo.clear()
        self.signature_size_combo.addItems(eng.SIGNATURE_SIZE_OPTIONS[imp_type])
        self.signature_size_combo.blockSignals(False)
        self._refresh_preview_and_mapping()

    def _on_sheet_mode_toggled(self, _checked: bool) -> None:
        is_preset = self.radio_preset.isChecked()
        self.a_series_combo.setEnabled(is_preset)
        self.custom_width.setEnabled(not is_preset)
        self.custom_height.setEnabled(not is_preset)
        self.unit_combo.setEnabled(not is_preset)
        self._refresh_preview_and_mapping()

    # ------------------------------------------------------------------ #
    # Preview + mapping refresh
    # ------------------------------------------------------------------ #

    def _current_selection(self):
        imp_type: ImpositionType = self.imposition_type_combo.currentData()
        sig_label = self.signature_size_combo.currentText()
        return imp_type, sig_label

    def _refresh_preview_and_mapping(self, *_args) -> None:
        imp_type, sig_label = self._current_selection()
        if not sig_label:
            return
        try:
            layout = eng.resolve_layout(imp_type, sig_label)
        except ValueError as exc:
            self.mapping_preview.setPlainText(str(exc))
            return

        side = self.side_combo.currentText().lower()
        self.preview_view.render_layout(layout, side=side)

        total_pages = self.source_info.page_count if self.source_info else None
        preview_text = eng.format_mapping_preview(imp_type, sig_label, total_input_pages=total_pages)

        w_mm, h_mm = self._current_sheet_size_mm()
        preview_text += f"\nSheet size      : {w_mm:.1f} x {h_mm:.1f} mm"
        preview_text += f"\nCreep thickness : {self.thickness_spin.value():.3f} mm/sheet"
        self.mapping_preview.setPlainText(preview_text)

    def _current_sheet_size_mm(self):
        if self.radio_preset.isChecked():
            return A_SERIES_MM[self.a_series_combo.currentText()]
        w, h = self.custom_width.value(), self.custom_height.value()
        if self.unit_combo.currentText() == "inches":
            w, h = w * 25.4, h * 25.4
        return (w, h)

    # ------------------------------------------------------------------ #
    # File actions
    # ------------------------------------------------------------------ #

    def _on_browse_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Source PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        self.source_path = path
        self.source_label.setText(os.path.basename(path))

        if pdf_processor is None:
            self.status_label.setText("PyMuPDF not available — page count unknown.")
            self.source_info = None
        else:
            try:
                self.source_info = pdf_processor.inspect_source(path)
                self.status_label.setText(
                    f"{self.source_info.page_count} pages · "
                    f"color hint: {self.source_info.color_space_hint}"
                )
            except Exception as exc:  # noqa: BLE001
                self.source_info = None
                self.status_label.setText(f"Could not read file: {exc}")

        self._refresh_preview_and_mapping()

    def _on_run_imposition(self) -> None:
        if not self.source_path:
            QMessageBox.warning(self, "No source file", "Please select a source PDF first.")
            return
        if pdf_processor is None:
            QMessageBox.critical(self, "Missing dependency", "PyMuPDF (fitz) is not installed.")
            return

        out_path, _ = QFileDialog.getSaveFileName(self, "Export Imposed PDF", "", "PDF Files (*.pdf)")
        if not out_path:
            return

        imp_type, sig_label = self._current_selection()
        w_mm, h_mm = self._current_sheet_size_mm()
        sheet_w_pt = w_mm * MM_TO_PT
        sheet_h_pt = h_mm * MM_TO_PT
        margin_pt = self.margin_spin.value() * MM_TO_PT
        gutter_pt = self.gutter_spin.value() * MM_TO_PT
        thickness_mm = self.thickness_spin.value()

        self.status_label.setText("Imposing…")
        QApplication.processEvents()

        try:
            pdf_processor.build_imposed_pdf(
                source_path=self.source_path,
                output_path=out_path,
                imposition_type=imp_type,
                signature_label=sig_label,
                sheet_width_pt=sheet_w_pt,
                sheet_height_pt=sheet_h_pt,
                margin_pt=margin_pt,
                gutter_pt=gutter_pt,
                paper_thickness_mm=thickness_mm,
            )
            self.status_label.setText(f"Exported: {out_path}")
            QMessageBox.information(self, "Done", f"Imposed PDF written to:\n{out_path}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.status_label.setText("Export failed.")
            QMessageBox.critical(self, "Imposition failed", str(exc))


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
