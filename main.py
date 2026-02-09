from fastapi import FastAPI, UploadFile, File
import pdfplumber
import io
import re
from datetime import datetime

app = FastAPI()

def clean(val): return val if val is not None else ""

def fmt(val, is_pct):
    if val is None: return ""
    if is_pct: return f"{val:.2f}%"
    return f"{int(val):,}".replace(',', ' ')

@app.post("/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    content = await file.read()
    text = ""
    # نقراو كلشي دقة وحدة
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    
    flat_text = text.replace('\n', ' ')

    # ---------------------------------------------------------
    # 1. VOTING SHEET (THE ULTIMATE FIX) 🕵️‍♂️
    # ---------------------------------------------------------
    voting_sheet = ""
    
    # الطريقة 1: البحث عن الصيغة القياسية (V12345/01) وخا يكونو فيها Espaces
    # كنقلبو على حرف V، موراه أرقام، موراه سلاش، موراه أرقام
    v_code_pattern = r"\b(V\s*[\d]{3,8}\s*/\s*[\d]{2})\b"
    v_match = re.search(v_code_pattern, text, re.IGNORECASE)
    
    if v_match:
        # لقيناه! نحيدو منو الفراغات ونردوه Majuscule
        voting_sheet = v_match.group(1).replace(" ", "").upper()
    else:
        # الطريقة 2: البحث عن السياق (ما وراء النقطتين)
        # كنقلبو على voting sheet وموراها أي نص كيبدا برقم أو حرف
        context_match = re.search(r"voting\s+sheet.*?:?\s*([A-Z0-9][A-Z0-9\-\/\s]+)", text, re.IGNORECASE)
        if context_match:
             candidate = context_match.group(1).strip()
             # تأكد أن النتيجة ماشي طويلة بزاف (باش ما يهزش جملة)
             if len(candidate) < 20:
                 voting_sheet = candidate

    # ---------------------------------------------------------
    # باقي المعلومات (كما هي)
    # ---------------------------------------------------------
    
    # Draft Number (D12345/01)
    draft_number = ""
    d_match = re.search(r"\b(D\s*[\d]{3,8}\s*/\s*[\d]{2})\b", text, re.IGNORECASE)
    if d_match:
        draft_number = d_match.group(1).replace(" ", "").upper()
    else:
        # Backup
        d_context = re.search(r"draft\s+implementing\s+act/measure.*?:?\s*([D0-9\/]+)", text, re.IGNORECASE)
        if d_context: draft_number = d_context.group(1).strip()

    # Procedure
    procedure_match = re.search(r"Type\s+of\s+procedure\s*[:\.]?\s*(.*)", text, re.IGNORECASE)
    procedure = procedure_match.group(1).strip() if procedure_match else ""

    # Date
    date_opinion = ""
    date_match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})", text)
    if date_match:
        date_opinion = date_match.group(1).replace('-', '/')

    # Consensus
    consensus_match = re.search(r"Consensus\s*[:\.]?\s*(.*)", text, re.IGNORECASE)
    consensus = consensus_match.group(1).strip() if consensus_match else ""


    # ---------------------------------------------------------
    # 2. NUMBERS & LOGIC (V10 logic maintained)
    # ---------------------------------------------------------
    
    # Helper for extraction
    def get_val(pattern, src_text):
        m = re.search(pattern, src_text, re.IGNORECASE | re.DOTALL)
        if not m: return None, False, ""
        
        raw = m.group(1).strip()
        if raw in ["-", "–", "—"]: return 0.0, True, "0"
        
        is_pct = "%" in m.group(0) or "%" in raw
        clean = raw.replace('%', '').replace(' ', '')
        if "," in clean and "." not in clean:
             if len(clean.split(",")[1]) == 2: clean = clean.replace(',', '.')
             else: clean = clean.replace(',', '')
        elif "," in clean: clean = clean.replace(',', '')
        
        try: return float(clean), is_pct, raw
        except: return None, False, ""

    # Extract
    num_for, _, num_for_s = get_val(r"Number of Member States in favour\s*:\s*([\d\-]+)", text)
    num_ag, _, num_ag_s = get_val(r"Number of Member States against\s*:\s*([\d\-]+)", text)
    num_abs, _, num_abs_s = get_val(r"Number of abstentions\s*:\s*([\d\-]+)", text)
    num_not, _, num_not_s = get_val(r"Number of Member States not represented\s*:\s*([\d\-]+)", text)

    pop_for, pct_for, _ = get_val(r"in favour.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_ag, pct_ag, _ = get_val(r"against.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_abs, pct_abs, _ = get_val(r"abstentions.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)

    # Logic
    final_absent_pop = ""
    final_sum_num = ""
    final_sum_pop = ""
    
    # Outputs strings
    o_for, o_ag, o_abs, o_not = "", "", "", ""
    p_for, p_ag, p_abs = "", "", ""

    # Fix zeros
    if num_ag == 0 and pop_ag is None: pop_ag = 0.0
    if num_abs == 0 and pop_abs is None: pop_abs = 0.0

    has_data = (pop_for is not None or pop_ag is not None or pop_abs is not None)
    
    if has_data:
        # Check huge numbers
        is_huge = False
        for v in [pop_for, pop_ag, pop_abs]:
            if v is not None and v > 100: is_huge = True; break
        
        is_pct_mode = (pct_for or pct_ag or pct_abs) and not is_huge
        
        c_for = pop_for if pop_for else 0.0
        c_ag = pop_ag if pop_ag else 0.0
        c_abs = pop_abs if pop_abs else 0.0

        if is_pct_mode:
            calc_absent = 100.0 - (c_for + c_ag + c_abs)
            if calc_absent < 0.01: calc_absent = 0.0
            final_absent_pop = f"{calc_absent:.2f}%"
            final_sum_pop = "100.00%"
            
            p_for = fmt(pop_for, True)
            p_ag = fmt(pop_ag, True) if pop_ag is not None else ""
            p_abs = fmt(pop_abs, True) if pop_abs is not None else ""
        else:
            final_absent_pop = ""
            total_pop = c_for + c_ag + c_abs
            final_sum_pop = fmt(total_pop, False)
            
            p_for = fmt(pop_for, False) if pop_for is not None else ""
            p_ag = fmt(pop_ag, False) if pop_ag is not None else ""
            p_abs = fmt(pop_abs, False) if pop_abs is not None else ""

    # Populate strings if they exist
    o_for = clean(num_for_s)
    o_ag = clean(num_ag_s)
    o_abs = clean(num_abs_s)
    o_not = clean(num_not_s)

    if all(x is not None for x in [num_for, num_ag, num_abs, num_not]):
        final_sum_num = str(int(num_for + num_ag + num_abs + num_not))


    return {
        "status": "success",
        "voting_sheet_number": voting_sheet,
        "type_of_procedure": procedure,
        "number_of_draft": draft_number,
        "date_of_opinion": date_opinion,
        "consensus": consensus,
        
        "for_number": o_for,
        "for_population": p_for,
        
        "against_number": o_ag,
        "against_population": p_ag,
        
        "abstain_number": o_abs,
        "abstain_population": p_abs,
        
        "absent_number": o_not,
        "absent_population": final_absent_pop,
        
        "sum_number": final_sum_num,
        "sum_population": final_sum_pop,
        "date_of_processing": datetime.now().strftime("%d-%m-%Y")
    }
