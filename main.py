from fastapi import FastAPI, UploadFile, File
import pdfplumber
import io
import re
from datetime import datetime

app = FastAPI()

def extract_text(pattern, text):
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else ""

def extract_val_info(pattern, text):
    """
    تستخرج القيمة، هل هي نسبة، والنص الأصلي
    """
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        raw_str = match.group(1).strip()
        
        # حالة 0
        if raw_str in ["-", "–", "—"]:
            return 0.0, False, "0" # False مؤقتاً، سنحدد النوع لاحقاً
            
        # هل هي نسبة؟
        is_pct = "%" in match.group(0) or "%" in raw_str
        
        clean_str = raw_str.replace('%', '').replace(' ', '')
        # تنظيف الفواصل
        if "," in clean_str and "." not in clean_str:
             if len(clean_str.split(",")[1]) == 2: 
                 clean_str = clean_str.replace(',', '.')
             else:
                 clean_str = clean_str.replace(',', '')
        elif "," in clean_str:
            clean_str = clean_str.replace(',', '')

        try:
            return float(clean_str), is_pct, raw_str
        except ValueError:
            return None, False, ""
            
    return None, False, ""

@app.post("/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    content = await file.read()
    text = ""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    
    flat_text = text.replace('\n', ' ')

    # --- 1. Metadata ---
    vs_match = re.search(r"\b([VD]\d{5,8}/\d{2})\b", text)
    if vs_match:
        voting_sheet = vs_match.group(1)
    else:
        voting_sheet = extract_text(r"(?:RegCom\s+)?(?:number\s+of\s+)?voting\s+sheet\s*[:\.]?\s*([A-Za-z0-9\-/]+)", text)

    procedure = extract_text(r"Type\s+of\s+procedure\s*[:\.]?\s*(.*)", text)
    draft_number = extract_text(r"(?:RegCom\s+)?Number\s+of\s+draft\s+implementing\s+act/measure\s*[:\.]?\s*(.*)", text)
    
    # Date logic
    date_match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})", text)
    date_opinion = date_match.group(1).replace('-', '/') if date_match else ""
    
    consensus = extract_text(r"Consensus\s*[:\.]?\s*(.*)", text)

    # --- 2. Extraction ---
    
    # Numbers
    num_for, _, num_for_s = extract_val_info(r"Number of Member States in favour\s*:\s*([\d\-]+)", text)
    num_ag, _, num_ag_s = extract_val_info(r"Number of Member States against\s*:\s*([\d\-]+)", text)
    num_abs, _, num_abs_s = extract_val_info(r"Number of abstentions\s*:\s*([\d\-]+)", text)
    num_not, _, num_not_s = extract_val_info(r"Number of Member States not represented\s*:\s*([\d\-]+)", text)

    # Populations
    pop_for, is_pct_for, pop_for_s = extract_val_info(r"in favour.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_ag, is_pct_ag, pop_ag_s = extract_val_info(r"against.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_abs, is_pct_abs, pop_abs_s = extract_val_info(r"abstentions.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)

    # --- 3. Logic & Calculations (The Fix) ---

    final_absent_pop = ""
    final_sum_num = ""
    final_sum_pop = ""
    
    # Helper formatting function
    def fmt(val, is_pct):
        if val is None: return ""
        if is_pct: return f"{val:.2f}%"
        return f"{int(val):,}".replace(',', ' ')

    # A. SUM Numbers (Always calculate if components exist)
    if all(x is not None for x in [num_for, num_ag, num_abs, num_not]):
        final_sum_num = str(int(num_for + num_ag + num_abs + num_not))

    # B. Population Logic
    if all(x is not None for x in [pop_for, pop_ag, pop_abs]):
        
        # Check if ANY of the values is a percentage (e.g., 94.33%)
        # This fixes the issue where "0" doesn't have a %, breaking the logic
        is_percentage_mode = is_pct_for or is_pct_ag or is_pct_abs
        
        if is_percentage_mode:
            # Calculate Absent
            calc_absent = 100.0 - (pop_for + pop_ag + pop_abs)
            if calc_absent < 0.01: calc_absent = 0.0
            
            final_absent_pop = f"{calc_absent:.2f}%"
            final_sum_pop = "100.00%"
            
            # Format outputs with %
            pop_for_s = fmt(pop_for, True)
            pop_ag_s = fmt(pop_ag, True)
            pop_abs_s = fmt(pop_abs, True)
            
        else:
            # Absolute Numbers
            final_absent_pop = "" # Keep blank
            total_pop = pop_for + pop_ag + pop_abs
            final_sum_pop = fmt(total_pop, False)
            
            # Format outputs as numbers
            pop_for_s = fmt(pop_for, False)
            pop_ag_s = fmt(pop_ag, False)
            pop_abs_s = fmt(pop_abs, False)

    def clean(val): return val if val is not None else ""

    return {
        "status": "success",
        "voting_sheet_number": voting_sheet,
        "type_of_procedure": procedure,
        "number_of_draft": draft_number,
        "date_of_opinion": date_opinion,
        "consensus": consensus,
        
        "for_number": clean(num_for_s),
        "for_population": clean(pop_for_s),
        
        "against_number": clean(num_ag_s),
        "against_population": clean(pop_ag_s),
        
        "abstain_number": clean(num_abs_s),
        "abstain_population": clean(pop_abs_s),
        
        "absent_number": clean(num_not_s),
        "absent_population": final_absent_pop,
        
        "sum_number": final_sum_num,
        "sum_population": final_sum_pop,
        "date_of_processing": datetime.now().strftime("%d-%m-%Y")
    }