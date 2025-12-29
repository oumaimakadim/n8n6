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

    # --- 2. Extraction & Logic ---
    
    # Extract Numbers (States)
    num_for_val, _, num_for_str = extract_val_info(r"Number of Member States in favour\s*:\s*([\d\-]+)", text)
    num_ag_val, _, num_ag_str = extract_val_info(r"Number of Member States against\s*:\s*([\d\-]+)", text)
    num_abs_val, _, num_abs_str = extract_val_info(r"Number of abstentions\s*:\s*([\d\-]+)", text)
    num_not_val, _, num_not_str = extract_val_info(r"Number of Member States not represented\s*:\s*([\d\-]+)", text)

    # Extract Populations
    pop_for_val, pop_for_is_pct, pop_for_str = extract_val_info(r"in favour.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_ag_val, pop_ag_is_pct, pop_ag_str = extract_val_info(r"against.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    pop_abs_val, pop_abs_is_pct, pop_abs_str = extract_val_info(r"abstentions.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)

    # --- 3. Final Calculation Logic ---
    
    final_absent_pop = ""  # Default is BLANK
    final_sum_num = ""     # Default is BLANK
    final_sum_pop = ""     # Default is BLANK
    
    # A. Logic for Populations (Absent & Sum)
    if (pop_for_val is not None and pop_ag_val is not None and pop_abs_val is not None):
        
        # CASE 1: They are Percentages (Normal Case)
        if pop_for_is_pct and pop_ag_is_pct and pop_abs_is_pct:
            calc_absent = 100.0 - (pop_for_val + pop_ag_val + pop_abs_val)
            if calc_absent < 0.01: calc_absent = 0.0
            
            final_absent_pop = f"{calc_absent:.2f}%"
            final_sum_pop = "100.00%"
            
            # Ensure inputs have % format
            if "%" not in pop_for_str: pop_for_str += "%"
            if "%" not in pop_ag_str: pop_ag_str += "%"
            if "%" not in pop_abs_str: pop_abs_str += "%"

        
        else:
            
            final_absent_pop = ""
            
            # Calculate Sum of absolute numbers
            total_pop = pop_for_val + pop_ag_val + pop_abs_val
            final_sum_pop = f"{int(total_pop):,}".replace(',', ' ') # Format nicely
            
            # Keep inputs as raw numbers (no %)

    # B. Logic for Numbers (Sum)
    if (num_for_val is not None and num_ag_val is not None and num_abs_val is not None and num_not_val is not None):
        total_num = int(num_for_val + num_ag_val + num_abs_val + num_not_val)
        final_sum_num = str(total_num)

    # Helper to return blank if None
    def ret(val): return val if val is not None else ""

    return {
        "status": "success",
        "voting_sheet_number": voting_sheet,
        "type_of_procedure": procedure,
        "number_of_draft": draft_number,
        "date_of_opinion": date_opinion,
        "consensus": consensus,
        
        # Numbers (States) - returns "" if missing
        "for_number": ret(num_for_str),
        "against_number": ret(num_ag_str),
        "abstain_number": ret(num_abs_str),
        "absent_number": ret(num_not_val), 
        # Populations - returns "" if missing
        "for_population": ret(pop_for_str),
        "against_population": ret(pop_ag_str),
        "abstain_population": ret(pop_abs_str),
        
        # Calculated / Blank fields
        "absent_population": final_absent_pop,
        "sum_number": final_sum_num,
        "sum_population": final_sum_pop,
        
        "date_of_processing": datetime.now().strftime("%d-%m-%Y")
    }