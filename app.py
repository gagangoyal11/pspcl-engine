import os
import re
import math
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber

app = FastAPI(title="PSPCL RDSS AutoCAD JSR Engine")

# CORS allow taaki mobile web app bina kisi issue ke connect ho sake
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def parse_autocad_pdf(file_path):
    with pdfplumber.open(file_path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(keep_blank_chars=False)

    full_text = " ".join([w["text"] for w in words])

    # 1. Header Information Extraction
    div_m = re.search(r"DIVISION\s*NAME\s*[:-]+([A-Z0-9\s]+?)(?:SUB|$)", full_text, re.IGNORECASE)
    sub_m = re.search(r"SUB\s*DIVISION\s*NAME\s*[:-]+([A-Z0-9\s]+?)(?:SUB|$)", full_text, re.IGNORECASE)
    fdr_m = re.search(r"FEEDER\s*NAME\s*[:-]+([A-Z0-9\s]+?)(?:ACTIVITY|$)", full_text, re.IGNORECASE)
    wo_m = re.search(r"W\.?O\s*NO\s*[:-]+([^\n]+?DATE[:-]+[^\s\n]+)", full_text, re.IGNORECASE)

    division = div_m.group(1).replace(":-", "").strip() if div_m else ""
    subdivision = sub_m.group(1).replace(":-", "").strip() if sub_m else ""
    feeder = fdr_m.group(1).replace(":-", "").strip() if fdr_m else "UNKNOWN FEEDER"
    wo_no = wo_m.group(1).strip() if wo_m else ""

    # 2. Coordinate Extraction for Poles and Spans
    structures = []
    spans = []

    # Pole regex (P1..P100, DP1, CP1, RP1, GOS1, DT/DTR)
    pole_regex = re.compile(r"^(P\d+|DP\d*|CP\d*|RP\d*|GOS\d*|DT\d*|DTR)$", re.IGNORECASE)

    for w in words:
        txt = w["text"].strip().upper()
        cx = (w["x0"] + w["x1"]) / 2.0
        cy = (w["top"] + w["bottom"]) / 2.0

        # Filter out schedule/table boxes: AutoCAD tables are usually on margins or corners
        if pole_regex.match(txt) and not txt.startswith("DP/202"):
            structures.append({"label": txt, "x": cx, "y": cy})
        else:
            # Span check: numeric values between 10m and 150m
            try:
                val = float(txt)
                # Ignore year numbers (2024, 2025, 2026) and tiny index numbers
                if 10.0 <= val <= 140.0 and val not in [2024, 2025, 2026]:
                    spans.append({"span": str(val), "val": val, "x": cx, "y": cy})
            except ValueError:
                continue

    # 3. Spatial Line-Chaining (Euclidean Nearest Neighbor)
    # Sort or link each structure with its preceding nearest incoming span
    used_spans = set()
    result_rows = []

    for struct in structures:
        # Find nearest span within physical proximity (radius)
        best_span = ""
        best_dist = float("inf")
        best_idx = -1

        for i, sp in enumerate(spans):
            if i in used_spans:
                continue
            dist = math.hypot(struct["x"] - sp["x"], struct["y"] - sp["y"])
            # Threshold distance in CAD canvas points
            if dist < best_dist and dist < 220:
                best_dist = dist
                best_span = sp["span"]
                best_idx = i

        if best_idx != -1:
            used_spans.add(best_idx)

        result_rows.append({
            "poleNo": struct["label"],
            "span": best_span,
            "acsrType": "WEASEL",
            "extWires": 3,
            "disWires": 3
        })

    # Fallback to remaining spans if structures missing labels (creates MF rows)
    remaining_spans = [sp for i, sp in enumerate(spans) if i not in used_spans]
    for idx, sp in enumerate(remaining_spans):
        result_rows.append({
            "poleNo": f"MF (Point {len(result_rows) + 1})",
            "span": sp["span"],
            "acsrType": "WEASEL",
            "extWires": 3,
            "disWires": 3
        })

    return {
        "status": "success",
        "division": division,
        "subdivision": subdivision,
        "feeder": feeder,
        "wo_no": wo_no,
        "total_structures": len(result_rows),
        "records": result_rows
    }

@app.post("/api/parse-drawing")
async def parse_drawing(file: UploadFile = File(...)):
    temp_path = f"temp_{file.filename}"
    try:
        with open(temp_path, "wb") as f:
            f.write(await file.read())
        data = parse_autocad_pdf(temp_path)
        return data
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
