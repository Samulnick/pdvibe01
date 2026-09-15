# pdvibe

Production-oriented PDF imposition tool for saddle-stitch booklet work,
built to hand off vector-intact, font-intact, color-space-intact output
files that open cleanly in CorelDRAW.

## Run

```
pip install -r requirements.txt
python ui_main.py
```

## Files

- `imposition_engine.py` — pure-math core: the exact signature routing
  arrays from the spec (16-page Standard, 8-page 2-Up), a documented
  generic fallback for the two under-specified sizes (standalone 8-page,
  and 4-page in both modes), padding, creep, and grid geometry.
- `pdf_processor.py` — PyMuPDF-based page transplant (`show_pdf_page`)
  that never rasterizes to a flat bitmap and never forces a color-space
  conversion.
- `ui_main.py` — PyQt6 dark-theme UI: cascading Imposition Type ->
  Signature Size dropdowns, ISO/custom sheet size, creep + margin
  controls, a live schematic preview, and the read-only mapping console.

## Note on the two generated (non-literal) layouts

The spec gives exact page-routing numbers for the 16-page Standard preset
and the 8-page 2-Up preset — those are transcribed verbatim as literal
constants. It doesn't give numbers for the standalone 8-page (Standard
mode) or either 4-page preset, so those are generated from a documented
"N-offset block" building routine consistent with the given examples.
If your prepress workflow expects a different exact page order for those
two, tell me the target order and I'll hardcode it the same way as the
other two.
