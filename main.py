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
    تستخرج القيمة، وهل هي نسبة مئوية
    """
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        raw_str = match.group(1).strip()
        
        # حالة الشرطة "-" تعني 0
        if raw_str in ["-", "–", "—"]:
            return 0.0, False # 0 ليس فيه %
            
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
            return float(clean_str), is_pct
        except ValueError:
            return None, False
            
    return None, False

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
    
    raw_date = extract_text(r"Date\s+of\s+(?:delivery|vote|opinion).*?[:\.]?\s*(.*)", text)
    date_match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})", raw_date if raw_date else text)
    date_opinion = date_match.group(1).replace('-', '/') if date_match else ""
    
    consensus = extract_text(r"Consensus\s*[:\.]?\s*(.*)", text)

    # --- 2. Numbers (States) ---
    num_for, _ = extract_val_info(r"Number of Member States in favour\s*:\s*([\d\-]+)", text)
    num_ag, _ = extract_val_info(r"Number of Member States against\s*:\s*([\d\-]+)", text)
    num_abs, _ = extract_val_info(r"Number of abstentions\s*:\s*([\d\-]+)", text)
    num_not, _ = extract_val_info(r"Number of Member States not represented\s*:\s*([\d\-]+)", text)

    # --- 3. Populations (The Fix) ---
    pop_for, is_for_pct = extract_val_info(r"in favour.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_ag, is_ag_pct = extract_val_info(r"against.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_abs, is_abs_pct = extract_val_info(r"abstentions.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    
    # --- 4. Calculation Logic (Updated) ---
    
    # تهيئة المتغيرات
    final_for = ""
    final_ag = ""
    final_abs = ""
    final_absent_pop = ""
    final_sum_pop = ""

    # هل البيانات موجودة؟
    if (pop_for is not None and pop_ag is not None and pop_abs is not None):
        
        # الشرط الجديد: إذا كان أي واحد فيهم نسبة مئوية، نتعامل مع الكل كنسبة مئوية
        is_percentage_mode = is_for_pct or is_ag_pct or is_abs_pct
        
        if is_percentage_mode:
            # حساب الغائبين
            calc_absent = 100.0 - (pop_for + pop_ag + pop_abs)
            if calc_absent < 0.01: calc_absent = 0.0
            
            final_absent_pop = f"{calc_absent:.2f}%"
            final_sum_pop = "100.00%"
            
            # تنسيق المخرجات بـ %
            final_for = f"{pop_for:.2f}%"
            final_ag = f"{pop_ag:.2f}%"
            final_abs = f"{pop_abs:.2f}%"
            
        else:
            # أرقام مطلقة (Absolute Numbers)
            # لا نحسب Absent (تبقى خاوية)
            final_absent_pop = ""
            
            # نحسب المجموع (Sum is required now)
            total_sum = pop_for + pop_ag + pop_abs
            final_sum_pop = f"{int(total_sum):,}".replace(',', ' ')
            
            # تنسيق المخرجات كأرقام
            final_for = f"{int(pop_for):,}".replace(',', ' ')
            final_ag = f"{int(pop_ag):,}".replace(',', ' ')
            final_abs = f"{int(pop_abs):,}".replace(',', ' ')

    # Sum of States
    final_sum_num = ""
    if num_for is not None and num_ag is not None and num_abs is not None and num_not is not None:
        total = num_for + num_ag + num_abs + num_not
        final_sum_num = str(int(total))

    def clean_int(val): return int(val) if val is not None else ""

    return {
        "status": "success",
        "voting_sheet_number": voting_sheet,
        "type_of_procedure": procedure,
        "number_of_draft": draft_number,
        "date_of_opinion": date_opinion,
        "consensus": consensus,
        "for_number": clean_int(num_for),
        "for_population": final_for,
        "against_number": clean_int(num_ag),
        "against_population": final_ag,
        "abstain_number": clean_int(num_abs),
        "abstain_population": final_abs,
        "absent_number": clean_int(num_not),
        "absent_population": final_absent_pop,
        "sum_number": final_sum_num,
        "sum_population": final_sum_pop, # دابا كتحسب فكل الحالات
        "date_of_processing": datetime.now().strftime("%d-%m-%Y")
    }