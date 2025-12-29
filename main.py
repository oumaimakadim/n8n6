from fastapi import FastAPI, UploadFile, File
import pdfplumber
import io
import re
from datetime import datetime

app = FastAPI()

def extract_value_and_type(pattern, text):
    
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        val_str = match.group(1).strip()
        
       
        if val_str in ["-", "–", "—"]:
            return 0.0, True 
            
       
        is_percentage = "%" in match.group(0)
        
     
        clean_str = val_str.replace(',', '.').replace('%', '').replace(' ', '')
        
        try:
            return float(clean_str), is_percentage
        except ValueError:
            return None, False
    return None, False

def extract_text(pattern, text):
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else ""

@app.post("/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    content = await file.read()
    text = ""
    
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
            
    
    flat_text = text.replace('\n', ' ')

 
    vs_match = re.search(r"\b(V\d{3,8}\/\d{2})\b", text)
    if vs_match:
        voting_sheet = vs_match.group(1)
    else:
        voting_sheet = extract_text(r"(?:RegCom\s+)?(?:number\s+of\s+)?voting\s+sheet\s*[:\.]?\s*([A-Za-z0-9\-/]+)", text)

    procedure = extract_text(r"Type\s+of\s+procedure\s*[:\.]?\s*(.*)", text)
    draft_number = extract_text(r"(?:RegCom\s+)?Number\s+of\s+draft\s+implementing\s+act/measure\s*[:\.]?\s*(.*)", text)
    date_opinion = extract_text(r"Date\s+of\s+(?:delivery|vote|opinion).*?[:\.]?\s*(.*)", text)
    consensus = extract_text(r"Consensus\s*[:\.]?\s*(.*)", text)

  
    for_num, _ = extract_value_and_type(r"Number of Member States in favour\s*:\s*([\d\-]+)", text)
    against_num, _ = extract_value_and_type(r"Number of Member States against\s*:\s*([\d\-]+)", text)
    abstain_num, _ = extract_value_and_type(r"Number of abstentions\s*:\s*([\d\-]+)", text)
    absent_num, _ = extract_value_and_type(r"Number of Member States not represented\s*:\s*([\d\-]+)", text)

    
    for_pop, is_for_pct = extract_value_and_type(r"representing a population of\s*:\s*([\d,\.\-\s]+%?)", text)
    against_pop, is_against_pct = extract_value_and_type(r"Number of Member States against.*?representing a population of\s*:\s*([\d,\.\-\s]+%?)", flat_text)
    abstain_pop, is_abstain_pct = extract_value_and_type(r"Number of abstentions.*?representing a population of\s*:\s*([\d,\.\-\s]+%?)", flat_text)

    
    
    absent_pop = "" 
    sum_num = ""
    sum_pop = ""
    if (for_pop is not None and against_pop is not None and abstain_pop is not None):
        if is_for_pct and is_against_pct and is_abstain_pct:
            
            calc_absent = 100.0 - (for_pop + against_pop + abstain_pop)
            if calc_absent < 0.01: calc_absent = 0.0
            absent_pop = f"{calc_absent:.2f}%"
            
            # Formatting outputs with %
            for_pop = f"{for_pop}%"
            against_pop = f"{against_pop}%"
            abstain_pop = f"{abstain_pop}%"
            
            # Sum Population
            sum_pop = "100.00%"
        else:
           
            absent_pop = "" 
            
            
    # Sum Numbers
    if for_num is not None and against_num is not None and abstain_num is not None and absent_num is not None:
        sum_num = for_num + against_num + abstain_num + absent_num
    else:
        sum_num = ""

    # تحويل None إلى "" (فراغ) للجدول
    def clean(val): return val if val is not None else ""

    return {
        "status": "success",
        "voting_sheet_number": voting_sheet,
        "type_of_procedure": procedure,
        "number_of_draft": draft_number,
        "date_of_opinion": date_opinion,
        "consensus": consensus,
        
        "for_number": clean(for_num),
        "for_population": clean(for_pop),
        
        "against_number": clean(against_num),
        "against_population": clean(against_pop),
        
        "abstain_number": clean(abstain_num),
        "abstain_population": clean(abstain_pop),
        
        "absent_number": clean(absent_num),
        "absent_population": absent_pop, 
        
        "sum_number": sum_num,
        "sum_population": sum_pop,
        "date_of_processing": datetime.now().strftime("%d-%m-%Y")
    }