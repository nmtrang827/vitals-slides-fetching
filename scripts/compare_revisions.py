from pathlib import Path
from pptx import Presentation

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"


def extract_slide_text(file_path):
    presentation = Presentation(file_path)
    slides = []

    for slide_number, slide in enumerate(presentation.slides, start=1):
        texts = []

        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text.strip()
                if text:
                    texts.append(text)

            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        text = cell.text.strip()
                        if text:
                            texts.append(text)

        slides.append({
            "slide": slide_number,
            "text": "\n".join(texts),
        })

    return slides


old_file = OUTPUT_DIR / "revision_535.pptx"
new_file = OUTPUT_DIR / "revision_536.pptx"

if not old_file.exists() or not new_file.exists():
    print("Missing PPTX file. Download both revisions first.")
    raise SystemExit(1)

old_slides = extract_slide_text(old_file)
new_slides = extract_slide_text(new_file)

print(f"Revision 535: {len(old_slides)} slides")
print(f"Revision 536: {len(new_slides)} slides")

max_slides = max(len(old_slides), len(new_slides))

for i in range(max_slides):
    old_text = old_slides[i]["text"] if i < len(old_slides) else None
    new_text = new_slides[i]["text"] if i < len(new_slides) else None

    if old_text != new_text:
        print(f"\n--- Slide {i + 1} changed ---")
        print("\n[Revision 535]")
        print(old_text or "(Slide missing)")
        print("\n[Revision 536]")
        print(new_text or "(Slide missing)")