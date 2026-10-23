import os
import re
import math
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import pdfplumber

app = FastAPI(title="PSPCL RDSS AutoCAD JSR Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return FileResponse("index.html")


def parse_autocad_pdf(file_path):
    with pdfplumber.open(file_path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(keep_blank_chars=False)

    full_text = " ".join([w["text"] for w in words])

    # 1. Feeder Name Extraction (Targeting DWG Title & Header)
    fdr_m = re.search(r"NAME\s*OF\s*FEEDER\s*[:-]+([^\n\r]+?)(?:PROJECT|PMA|JE|DWG|$)", full_text, re.IGNORECASE)
    div_m = re.search(r"DIVISION\s*NAME\s*[:-]+([A-Z0-9\s]+?)(?:SUB|$)", full_text, re.IGNORECASE)
    sub_m = re.search(r"SUB\s*DIVISION\s*NAME\s*[:-]+([A-Z0-9\s]+?)(?:SUB|$)", full_text, re.IGNORECASE)
    dwg_no_m = re.search(r"DWG\s*NO\.?\s*[:-]?\s*([A-Z0-9\/\.\-_]+)", full_text, re.IGNORECASE)

    feeder_name = fdr_m.group(1).replace(":-", "").strip() if fdr_m else ""
    if not feeder_name or len(feeder_name) < 3:
        # Fallback from filename
        fname = os.path.basename(file_path).upper()
        clean = re.sub(r"^[0-9\.\s_\-]+", "", fname).replace(".PDF", "").replace("-MODEL", "").strip()
        feeder_name = clean

    division = div_m.group(1).replace(":-", "").strip() if div_m else "JALALABAD"
    subdivision = sub_m.group(1).replace(":-", "").strip() if sub_m else "GURU HAR SAHAI"
    if "SU-GHS" in full_text:
        subdivision = "SUB-URBAN GURU HAR SAHAI"

    # 2. Extract Structures & Numeric Values with 2D Coordinates
    structures = []
    spans = []

    # Filter out year and legend numbers
    ignore_numbers = {2024, 2025, 2026, 11, 66, 132, 220, 150, 80, 50, 13, 20} # cable sizes / voltage / years

    pole_regex = re.compile(r"^(GOS\d*|RP\d*|DP\d*|CP\d*|P\d+|DTR?)$", re.IGNORECASE)

    for w in words:
        txt = w["text"].strip().upper()
        cx = (w["x0"] + w["x1"]) / 2.0
        cy = (w["top"] + w["bottom"]) / 2.0

        # Ignore Schedule Table located in top margin
        if cy < 160:
            continue

        if pole_regex.match(txt) and not txt.startswith("DP/202"):
            structures.append({"label": txt, "x": cx, "y": cy})
        else:
            try:
                val = float(txt)
                if 10.0 <= val <= 140.0:
                    # Filter out conductor size descriptions
                    if val in [13.0, 20.0, 50.0, 80.0] and "SQMM" in full_text:
                        continue
                    spans.append({"span": str(val), "val": val, "x": cx, "y": cy})
            except ValueError:
                continue

    # 3. Sort structures according to Physical Flow (Left-to-Right / Takeoff Point A)
    structures.sort(key=lambda s: (s["x"], s["y"]))

    # 4. Strict INCOMING Span Pairing (Preceding Vector Analysis)
    result_rows = []
    used_spans = set()

    for idx, struct in enumerate(structures):
        best_span = ""
        best_dist = float("inf")
        best_idx = -1

        # Pehle pichle pole se incoming vector check karo
        prev_x = structures[idx - 1]["x"] if idx > 0 else (struct["x"] - 50)
        prev_y = structures[idx - 1]["y"] if idx > 0 else (struct["y"] + 50)

        for s_idx, sp in enumerate(spans):
            if s_idx in used_spans:
                continue

            dist = math.hypot(struct["x"] - sp["x"], struct["y"] - sp["y"])
            
            # Distance must be within span label radius (typically 30-180 canvas units)
            if dist < best_dist and dist < 180:
                best_dist = dist
                best_span = sp["span"]
                best_idx = s_idx

        if best_idx != -1:
            used_spans.add(best_idx)

        # Skip duplicate DTR poles sitting on same coordinates
        if idx > 0 and struct["label"] == "DTR" and structures[idx-1]["label"] == "DTR":
            if math.hypot(struct["x"] - structures[idx-1]["x"], struct["y"] - structures[idx-1]["y"]) < 10:
                continue

        result_rows.append({
            "poleNo": struct["label"],
            "span": best_span,
            "acsrType": "WEASEL",
            "extWires": 3,
            "disWires": 3
        })

    return {
        "status": "success",
        "division": division,
        "subdivision": subdivision,
        "feeder": feeder_name,
        "wo_no": "105-A&B/RDSS/TE-94/EIC/DP/2024-2025/MUKTSAR DATE:-16.06.2025",
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
