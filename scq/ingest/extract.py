"""
Extract figures and their captions from a scientific PDF.

Module entry point: ``python -m scq.ingest.extract <pdf> <out_dir> [--prefix name]``.
The module also exposes ``main()`` so other modules (e.g. ``scq.ingest.process``)
can invoke the CLI without spawning a subprocess.

1. Scans PDF text for "Figure N" / "Fig. N" / "FIG. N" captions
2. Identifies which pages contain figures
3. Rasterizes those pages at high DPI
4. Attempts to crop individual figures from the page
5. Saves figure images + a captions.json mapping

Output:
    output_dir/
      {prefix}_fig1.jpg
      {prefix}_fig2.jpg
      ...
      captions.json   ← {"fig1": "Figure 1: ...", "fig2": "Figure 2: ..."}
"""

import json
import os
import re
import subprocess
import sys

# fitz (PyMuPDF) and PIL (Pillow) are optional runtime deps — only needed when
# actually extracting figures. Defer the import-time check to main() so just
# `from scq.ingest import extract` doesn't sys.exit(1) when the optional deps
# are missing. Same pattern as scq/ingest/mendeley.py — important because the
# CLI lazy-imports modules and a hard sys.exit at module-load time would crash
# unrelated `scq` commands.
fitz = None  # type: ignore[assignment]
Image = None  # type: ignore[assignment]


def _require_imaging():
    """Resolve fitz + Pillow on first use; emit a friendly error + exit if missing."""
    global fitz, Image
    if fitz is not None and Image is not None:
        return
    try:
        import fitz as _fitz
    except ImportError:
        print("ERROR: PyMuPDF not installed. Run: pip install PyMuPDF --break-system-packages")
        sys.exit(1)
    try:
        from PIL import Image as _Image
    except ImportError:
        print("ERROR: Pillow not installed. Run: pip install Pillow --break-system-packages")
        sys.exit(1)
    fitz = _fitz
    Image = _Image


def extract_captions(doc):
    """
    Scan all pages for figure captions. Returns dict:
    { fig_num: { "caption": "...", "page": page_idx } }
    """
    captions = {}
    # Patterns for figure captions in physics papers
    patterns = [
        r"((?:Figure|Fig\.|FIG\.)\s*(\d+)[.:]\s*(.+?)(?:\n\n|\Z))",
        r"((?:Figure|Fig\.|FIG\.)\s*(\d+)[.:]\s*(.+?)(?=(?:Figure|Fig\.|FIG\.)\s*\d+|$))",
    ]

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text = page.get_text("text")

        # Try multi-line caption extraction
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.DOTALL | re.IGNORECASE):
                fig_num = int(match.group(2))
                caption_text = match.group(1).strip()
                # Clean up caption: collapse whitespace, limit length
                caption_text = re.sub(r"\s+", " ", caption_text)
                if len(caption_text) > 500:
                    caption_text = caption_text[:497] + "..."

                if fig_num not in captions:
                    captions[fig_num] = {"caption": caption_text, "page": page_idx}

    return captions


def find_figure_pages(doc, captions):
    """
    Determine which pages contain figures.
    Uses caption locations + heuristics (pages with images).
    """
    fig_pages = set()

    # Pages from captions
    for info in captions.values():
        fig_pages.add(info["page"])

    # Also check for pages with large embedded images
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        images = page.get_images(full=True)
        for img in images:
            xref = img[0]
            try:
                pix = fitz.Pixmap(doc, xref)
                # Consider it a figure if image is reasonably large
                if pix.width > 200 and pix.height > 150:
                    fig_pages.add(page_idx)
                pix = None
            except Exception:
                # Unreadable/odd image object: skip it, keep scanning.
                pass

    return sorted(fig_pages)


def rasterize_page(pdf_path, page_num, dpi=200):
    """Rasterize a single page using pdftoppm. Returns PIL Image."""
    result = subprocess.run(
        [
            "pdftoppm",
            "-jpeg",
            "-r",
            str(dpi),
            "-f",
            str(page_num + 1),
            "-l",
            str(page_num + 1),
            str(pdf_path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        return None

    from io import BytesIO

    return Image.open(BytesIO(result.stdout))


def extract_figure_regions(page_img, doc, page_idx):
    """
    Try to extract individual figure regions from a rasterized page.
    Uses PyMuPDF image bounding boxes as guides.
    Falls back to returning the full page if detection fails.
    """
    page = doc[page_idx]
    pw, ph = page.rect.width, page.rect.height
    iw, ih = page_img.size

    regions = []
    images = page.get_images(full=True)

    for img_info in images:
        xref = img_info[0]
        try:
            # Find where this image is placed on the page
            rects = page.get_image_rects(xref)
            for rect in rects:
                # Convert PDF coords to pixel coords
                x0 = int(rect.x0 / pw * iw)
                y0 = int(rect.y0 / ph * ih)
                x1 = int(rect.x1 / pw * iw)
                y1 = int(rect.y1 / ph * ih)

                # Only keep reasonably sized regions
                rw, rh = x1 - x0, y1 - y0
                if rw > 100 and rh > 80:
                    # Expand slightly to capture borders/labels
                    pad_x = int(rw * 0.03)
                    pad_y = int(rh * 0.05)
                    x0 = max(0, x0 - pad_x)
                    y0 = max(0, y0 - pad_y)
                    x1 = min(iw, x1 + pad_x)
                    y1 = min(ih, y1 + pad_y)
                    regions.append((x0, y0, x1, y1))
        except Exception:
            # Bad image on this page: caption-anchored fallback still runs.
            pass

    # Merge overlapping regions
    if regions:
        regions = merge_regions(regions)

    return regions


def merge_regions(regions, overlap_thresh=0.3):
    """Merge overlapping bounding boxes."""
    if not regions:
        return regions

    regions = sorted(regions, key=lambda r: (r[1], r[0]))
    merged = [list(regions[0])]

    for r in regions[1:]:
        last = merged[-1]
        # Check vertical overlap
        overlap_y = max(0, min(last[3], r[3]) - max(last[1], r[1]))
        h = max(last[3] - last[1], r[3] - r[1])
        if h > 0 and overlap_y / h > overlap_thresh:
            # Merge
            last[0] = min(last[0], r[0])
            last[1] = min(last[1], r[1])
            last[2] = max(last[2], r[2])
            last[3] = max(last[3], r[3])
        else:
            merged.append(list(r))

    return [tuple(r) for r in merged]


def save_figure(img, path, max_width=800, quality=70):
    """Resize and save as JPEG."""
    w, h = img.size
    if w > max_width:
        ratio = max_width / w
        img = img.resize((max_width, int(h * ratio)), Image.LANCZOS)
    img.save(path, "JPEG", quality=quality, optimize=True)


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 extract_figures.py <paper.pdf> <output_dir/> [--prefix name]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    out_dir = sys.argv[2]
    prefix = "fig"

    if "--prefix" in sys.argv:
        idx = sys.argv.index("--prefix")
        if idx + 1 < len(sys.argv):
            prefix = sys.argv[idx + 1]

    if not os.path.exists(pdf_path):
        print(f"ERROR: File not found: {pdf_path}")
        sys.exit(1)

    # Resolve fitz + Pillow at the point of first real use, after arg checks.
    _require_imaging()

    os.makedirs(out_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    print(f"Opened: {pdf_path} ({len(doc)} pages)")

    # Step 1: Extract captions from text
    captions = extract_captions(doc)
    print(f"Found {len(captions)} figure captions")

    # Step 2: Find pages with figures
    fig_pages = find_figure_pages(doc, captions)
    print(f"Figure pages: {[p + 1 for p in fig_pages]}")

    # Step 3: Process each figure
    results = {}
    fig_counter = 0

    if captions:
        # We have captions — process in figure-number order
        for fig_num in sorted(captions.keys()):
            page_idx = captions[fig_num]["page"]
            fig_counter += 1

            page_img = rasterize_page(pdf_path, page_idx)
            if page_img is None:
                print(f"  Warning: couldn't rasterize page {page_idx + 1}")
                continue

            # Try to extract individual figure regions
            regions = extract_figure_regions(page_img, doc, page_idx)

            if regions and len(regions) == 1:
                # Single figure on page — crop it
                crop = page_img.crop(regions[0])
            elif not regions:
                # Fallback: use full page
                crop = page_img
            else:
                # Multiple regions — use the full page (composite figure)
                crop = page_img

            fname = f"{prefix}_fig{fig_num}.jpg"
            save_figure(crop, os.path.join(out_dir, fname))
            print(f"  Saved: {fname} (page {page_idx + 1})")

            results[f"fig{fig_num}"] = {
                "file": fname,
                "page": page_idx + 1,
                "caption": captions[fig_num]["caption"],
            }
    else:
        # No captions found — fall back to extracting each figure page
        for page_idx in fig_pages:
            fig_counter += 1
            page_img = rasterize_page(pdf_path, page_idx)
            if page_img is None:
                continue

            fname = f"{prefix}_fig{fig_counter}.jpg"
            save_figure(page_img, os.path.join(out_dir, fname))
            print(f"  Saved: {fname} (page {page_idx + 1})")

            results[f"fig{fig_counter}"] = {
                "file": fname,
                "page": page_idx + 1,
                "caption": f"Page {page_idx + 1}",
            }

    # Save captions mapping
    captions_path = os.path.join(out_dir, "captions.json")
    with open(captions_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nDone: {len(results)} figures extracted to {out_dir}/")
    print(f"Captions saved to: {captions_path}")

    doc.close()

    # Output JSON summary for Claude
    print("\n--- JSON ---")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
