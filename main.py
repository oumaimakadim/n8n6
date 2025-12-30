from fastapi import FastAPI, UploadFile, File
import pdfplumber
import io
import re
from datetime import datetime

app = FastAPI()

def extract_text(pattern, text):
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else ""

def clean_date(text):
    if not text:
        return ""
    match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})", text)
    if match:
        return match.group(1).replace('-', '/')
    return ""

def extract_val_info(pattern, text):
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        raw_str = match.group(1).strip()
        
        if raw_str in ["-", "–", "—"]:
            return 0.0, True, "0"
            
        is_pct = "%" in match.group(0) or "%" in raw_str
        
        clean_str = raw_str.replace('%', '').replace(' ', '')
        
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

def fmt(val, is_pct):
    if val is None: return ""
    if is_pct: return f"{val:.2f}%"
    return f"{int(val):,}".replace(',', ' ')

@app.post("/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    content = await file.read()
    text = ""
    
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    
    flat_text = text.replace('\n', ' ')

    vs_match = re.search(r"\b([VD]\d{5,8}/\d{2})\b", text)
    if vs_match:
        voting_sheet = vs_match.group(1)
    else:
        voting_sheet = extract_text(r"(?:RegCom\s+)?(?:number\s+of\s+)?voting\s+sheet\s*[:\.]?\s*([A-Za-z0-9\-/]+)", text)

    procedure = extract_text(r"Type\s+of\s+procedure\s*[:\.]?\s*(.*)", text)
    
    draft_match = re.search(r"\b(D\d{5,8}/\d{2})\b", text)
    if draft_match:
        draft_number = draft_match.group(1)
    else:
        draft_number = extract_text(r"(?:RegCom\s+)?Number\s+of\s+draft\s+implementing\s+act/measure\s*[:\.]?\s*(.*)", text)

    raw_date = extract_text(r"Date\s+of\s+(?:delivery|vote|opinion).*?[:\.]?\s*(.*)", text)
    date_opinion = clean_date(raw_date)
    if not date_opinion:
        date_opinion = clean_date(text)

    consensus = extract_text(r"Consensus\s*[:\.]?\s*(.*)", text)

    num_for_val, _, num_for_str = extract_val_info(r"Number of Member States in favour\s*:\s*([\d\-]+)", text)
    num_ag_val, _, num_ag_str = extract_val_info(r"Number of Member States against\s*:\s*([\d\-]+)", text)
    num_abs_val, _, num_abs_str = extract_val_info(r"Number of abstentions\s*:\s*([\d\-]+)", text)
    num_not_val, _, num_not_str = extract_val_info(r"Number of Member States not represented\s*:\s*([\d\-]+)", text)

    pop_for_val, pop_for_is_pct, pop_for_str = extract_val_info(r"in favour.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_ag_val, pop_ag_is_pct, pop_ag_str = extract_val_info(r"against.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_abs_val, pop_abs_is_pct, pop_abs_str = extract_val_info(r"abstentions.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)

    if num_ag_val == 0 and pop_ag_val is None:
        pop_ag_val = 0.0
    if num_abs_val == 0 and pop_abs_val is None:
        pop_abs_val = 0.0

    final_absent_pop = ""
    final_sum_num = ""
    final_sum_pop = ""
    
    pop_for_out = ""
    pop_ag_out = ""
    pop_abs_out = ""

    has_any_data = (pop_for_val is not None or pop_ag_val is not None or pop_abs_val is not None)
    
    if has_any_data:
        is_huge_number = False
        for val in [pop_for_val, pop_ag_val, pop_abs_val]:
            if val is not None and val > 100:
                is_huge_number = True
                break
        
        is_percentage_mode = (pop_for_is_pct or pop_ag_is_pct or pop_abs_is_pct) and not is_huge_number
        
        if is_percentage_mode:
            c_for = pop_for_val if pop_for_val is not None else 0.0
            c_ag = pop_ag_val if pop_ag_val is not None else 0.0
            c_abs = pop_abs_val if pop_abs_val is not None else 0.0
            
            calc_absent = 100.0 - (c_for + c_ag + c_abs)
            if calc_absent < 0.01: calc_absent = 0.0
            
            final_absent_pop = f"{calc_absent:.2f}%"
            final_sum_pop = "100.00%"
            
            pop_for_out = fmt(pop_for_val, True)
            pop_ag_out = fmt(pop_ag_val, True) if pop_ag_val is not None else ""
            pop_abs_out = fmt(pop_abs_val, True) if pop_abs_val is not None else ""
            
        else:
            final_absent_pop = ""
            
            total_pop = (pop_for_val or 0) + (pop_ag_val or 0) + (pop_abs_val or 0)
            final_sum_pop = fmt(total_pop, False)
            
            pop_for_out = fmt(pop_for_val, False) if pop_for_val is not None else ""
            pop_ag_out = fmt(pop_ag_val, False) if pop_ag_val is not None else ""
            pop_abs_out = fmt(pop_abs_val, False) if pop_abs_val is not None else ""

    if all(x is not None for x in [num_for_val, num_ag_val, num_abs_val, num_not_val]):
        final_sum_num = str(int(num_for_val + num_ag_val + num_abs_val + num_not_val))

    def clean(val): return val if val is not None else ""

    return {
        "status": "success",
        "voting_sheet_number": voting_sheet,
        "type_of_procedure": procedure,
        "number_of_draft": draft_number,
        "date_of_opinion": date_opinion,
        "consensus": consensus,
        
        "for_number": clean(num_for_str),
        "for_population": pop_for_out,
        
        "against_number": clean(num_ag_str),
        "against_population": pop_ag_out,
        
        "abstain_number": clean(num_abs_str),
        "abstain_population": pop_abs_out,
        
        "absent_number": clean(num_not_str),
        "absent_population": final_absent_pop,
        
        "sum_number": final_sum_num,
        "sum_population": final_sum_pop,
        "date_of_processing": datetime.now().strftime("%d-%m-%Y")
    }