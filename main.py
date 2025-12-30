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
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        raw_str = match.group(1).strip()
        if raw_str in ["-", "–", "—"]:
            return 0.0, False, "0"
            
        is_pct = "%" in match.group(0) or "%" in raw_str
        
        clean_str = raw_str.replace('%', '').replace(' ', '')
        if "," in clean_str and "." not in clean_str:
             if len(clean_str.split(",")[1]) == 2: clean_str = clean_str.replace(',', '.')
             else: clean_str = clean_str.replace(',', '')
        elif "," in clean_str: clean_str = clean_str.replace(',', '')

        try:
            return float(clean_str), is_pct, raw_str
        except ValueError:
            return None, False, ""
    return None, False, ""

def fmt(val, is_pct):
    if val is None: return ""
    if is_pct: return f"{val:.2f}%"
    # تنسيق الأرقام الكبيرة (45 102 881)
    return f"{int(val):,}".replace(',', ' ')

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
    voting_sheet = vs_match.group(1) if vs_match else extract_text(r"(?:RegCom\s+)?(?:number\s+of\s+)?voting\s+sheet\s*[:\.]?\s*([A-Za-z0-9\-/]+)", text)

    procedure = extract_text(r"Type\s+of\s+procedure\s*[:\.]?\s*(.*)", text)
    draft_number = extract_text(r"(?:RegCom\s+)?Number\s+of\s+draft\s+implementing\s+act/measure\s*[:\.]?\s*(.*)", text)
    
    date_match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})", text)
    date_opinion = date_match.group(1).replace('-', '/') if date_match else ""
    if not date_opinion: date_opinion = extract_text(r"Date\s+of\s+(?:delivery|vote|opinion).*?[:\.]?\s*(.*)", text)

    consensus = extract_text(r"Consensus\s*[:\.]?\s*(.*)", text)

    # --- 2. Extraction ---
    num_for, _, num_for_s = extract_val_info(r"Number of Member States in favour\s*:\s*([\d\-]+)", text)
    num_ag, _, num_ag_s = extract_val_info(r"Number of Member States against\s*:\s*([\d\-]+)", text)
    num_abs, _, num_abs_s = extract_val_info(r"Number of abstentions\s*:\s*([\d\-]+)", text)
    num_not, _, num_not_s = extract_val_info(r"Number of Member States not represented\s*:\s*([\d\-]+)", text)

    pop_for, is_pct_for, pop_for_s = extract_val_info(r"in favour.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_ag, is_pct_ag, pop_ag_s = extract_val_info(r"against.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_abs, is_pct_abs, pop_abs_s = extract_val_info(r"abstentions.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)

    # --- 3. Logic Fixes (التصحيح الصارم) ---

    # تعويض القيم المفقودة بـ 0.0 فقط للحساب
    if num_ag == 0 and pop_ag is None: pop_ag = 0.0
    if num_abs == 0 and pop_abs is None: pop_abs = 0.0

    final_absent_pop = ""
    final_sum_num = ""
    final_sum_pop = ""
    pop_for_out, pop_ag_out, pop_abs_out = "", "", ""

    has_any_data = (pop_for is not None or pop_ag is not None or pop_abs is not None)
    
    if has_any_data:
        # فحص: هل هناك أي رقم أكبر من 100؟ (إذا نعم، فهو ليس نسبة)
        is_huge_number = False
        for val in [pop_for, pop_ag, pop_abs]:
            if val is not None and val > 100:
                is_huge_number = True
                break
        
        # إذا كان الرقم كبيراً، نلغي وضع النسبة المئوية
        if is_huge_number:
            is_percentage_mode = False
        else:
            is_percentage_mode = (is_pct_for or is_pct_ag or is_pct_abs)
        
        if is_percentage_mode:
            # حساب النسبة (Absent)
            c_for = pop_for if pop_for is not None else 0.0
            c_ag = pop_ag if pop_ag is not None else 0.0
            c_abs = pop_abs if pop_abs is not None else 0.0
            
            calc_absent = 100.0 - (c_for + c_ag + c_abs)
            if calc_absent < 0.01: calc_absent = 0.0
            
            final_absent_pop = f"{calc_absent:.2f}%"
            final_sum_pop = "100.00%"
            
            pop_for_out = fmt(pop_for, True)
            pop_ag_out = fmt(pop_ag, True) if pop_ag is not None else ""
            pop_abs_out = fmt(pop_abs, True) if pop_abs is not None else ""
            
        else:
            # أرقام مطلقة (Absolute Numbers)
            final_absent_pop = ""  # نخليوها خاوية
            
            total_pop = (pop_for or 0) + (pop_ag or 0) + (pop_abs or 0)
            final_sum_pop = fmt(total_pop, False) # تنسيق كرقم عادي
            
            pop_for_out = fmt(pop_for, False)
            pop_ag_out = fmt(pop_ag, False)
            pop_abs_out = fmt(pop_abs, False)
    
    # Sum Numbers (States)
    if all(x is not None for x in [num_for, num_ag, num_abs, num_not]):
        final_sum_num = str(int(num_for + num_ag + num_abs + num_not))

    def clean(val): return val if val is not None else ""

    return {
        "status": "success",
        "voting_sheet_number": voting_sheet,
        "type_of_procedure": procedure,
        "number_of_draft": draft_number,
        "date_of_opinion": date_opinion,
        "consensus": consensus,
        
        "for_number": clean(num_for_s),
        "for_population": pop_for_out,
        
        "against_number": clean(num_ag_s),
        "against_population": pop_ag_out,
        
        "abstain_number": clean(num_abs_s),
        "abstain_population": pop_abs_out,
        
        "absent_number": clean(num_not_s),
        "absent_population": final_absent_pop,
        
        "sum_number": final_sum_num,
        "sum_population": final_sum_pop,
        "date_of_processing": datetime.now().strftime("%d-%m-%Y")
    }