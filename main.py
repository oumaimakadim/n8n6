from fastapi import FastAPI, UploadFile, File
import pdfplumber
import io
import re
from datetime import datetime

app = FastAPI()

def clean_date(text):
    """
    Extracts only the date pattern (DD/MM/YYYY or DD-MM-YYYY)
    and removes any surrounding text.
    """
    if not text:
        return ""
    # Regex to find date pattern
    match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})", text)
    if match:
        # Standardize to DD/MM/YYYY
        return match.group(1).replace('-', '/')
    return ""

def extract_value_and_type(pattern, text):
    """
    Extracts a numeric value and determines if it is a percentage.
    Returns: (value, is_percentage_bool)
    """
    # Use DOTALL to match across lines if necessary
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        val_str = match.group(1).strip()
        
        # Handle cases where "-" or "–" represents 0 or None
        if val_str in ["-", "–", "—"]:
            return 0.0, True 
            
        # Check if the original string contained a '%' sign
        # We look at the full match group to see context if needed, 
        # but usually checking val_str is enough if regex captures it.
        # Better: check the input text around the match or the match itself.
        is_percentage = "%" in match.group(0)
        
        # Clean the number (remove % and spaces)
        clean_str = val_str.replace('%', '').replace(' ', '')
        
        # Handle decimal commas vs thousand separators
        # Standardize: 99,79 -> 99.79
        if "," in clean_str and "." not in clean_str:
             if len(clean_str.split(",")[1]) == 2: 
                 clean_str = clean_str.replace(',', '.')
             else:
                 clean_str = clean_str.replace(',', '')
        elif "," in clean_str:
            clean_str = clean_str.replace(',', '')

        try:
            return float(clean_str), is_percentage
        except ValueError:
            return None, False
    return None, False

def extract_text(pattern, text):
    """Extracts text based on regex pattern."""
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""

@app.post("/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    content = await file.read()
    text = ""
    
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    
    # Flatten text to handle multi-line patterns easily
    flat_text = text.replace('\n', ' ')

    # --- 1. METADATA EXTRACTION ---

    # Logic to find Voting Sheet Number (handles Vxxxxx/xx format)
    vs_match = re.search(r"\b([VD]\d{5,8}/\d{2})\b", text)
    if vs_match:
        voting_sheet = vs_match.group(1)
    else:
        # Fallback: look for text after "voting sheet"
        voting_sheet = extract_text(r"(?:RegCom\s+)?(?:number\s+of\s+)?voting\s+sheet\s*[:\.]?\s*([A-Za-z0-9\-/]+)", text)

    procedure = extract_text(r"Type\s+of\s+procedure\s*[:\.]?\s*(.*)", text)
    
    draft_match = re.search(r"\b(D\d{5,8}/\d{2})\b", text)
    if draft_match:
        draft_number = draft_match.group(1)
    else:
        draft_number = extract_text(r"(?:RegCom\s+)?Number\s+of\s+draft\s+implementing\s+act/measure\s*[:\.]?\s*(.*)", text)

    # Date Cleaning
    raw_date = extract_text(r"Date\s+of\s+(?:delivery|vote|opinion).*?[:\.]?\s*(.*)", text)
    date_opinion = clean_date(raw_date)
    
    # Fallback date search
    if not date_opinion:
        date_opinion = clean_date(text)

    consensus = extract_text(r"Consensus\s*[:\.]?\s*(.*)", text)

    # --- 2. NUMERIC DATA EXTRACTION ---

    # Raw counts
    for_num, _ = extract_value_and_type(r"Number of Member States in favour\s*:\s*([\d\-]+)", text)
    against_num, _ = extract_value_and_type(r"Number of Member States against\s*:\s*([\d\-]+)", text)
    abstain_num, _ = extract_value_and_type(r"Number of abstentions\s*:\s*([\d\-]+)", text)
    absent_num, _ = extract_value_and_type(r"Number of Member States not represented\s*:\s*([\d\-]+)", text)

    # Populations (Value + Type Check)
    # The regex looks for the number following "representing a population of"
    # We use flat_text to ensure line breaks don't break the regex
    for_pop, is_for_pct = extract_value_and_type(r"in favour.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    against_pop, is_against_pct = extract_value_and_type(r"against.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    abstain_pop, is_abstain_pct = extract_value_and_type(r"abstentions.*?" + r"representing a population of\s*:\s*([\d,\.\s]+%?)", flat_text)
    
    # --- 3. CALCULATIONS & FORMATTING ---

    absent_pop = ""
    sum_pop = ""
    
    # Helper for formatting
    def fmt(val, add_pct_sign):
        if val is None: return ""
        if add_pct_sign: return f"{val:.2f}%"
        # Format large numbers with space as thousand separator, no decimals for people count
        return f"{int(val):,}".replace(',', ' ') 

    # Logic: Only calculate Absent/Sum if data exists
    if (for_pop is not None and against_pop is not None and abstain_pop is not None):
        
        # CASE A: Data is Percentages (e.g., 99.79%)
        if is_for_pct and is_against_pct and is_abstain_pct:
            calc_absent = 100.0 - (for_pop + against_pop + abstain_pop)
            if calc_absent < 0.01: calc_absent = 0.0
            
            absent_pop = f"{calc_absent:.2f}%"
            sum_pop = "100.00%"
            
            # Re-format inputs with %
            for_pop = fmt(for_pop, True)
            against_pop = fmt(against_pop, True)
            abstain_pop = fmt(abstain_pop, True)
            
        # CASE B: Data is Absolute Numbers (e.g., 45000000)
        else:
            # Do NOT calculate Absent population (leave blank)
            absent_pop = "" 
            
            # Sum the absolute numbers
            total_pop = for_pop + against_pop + abstain_pop
            sum_pop = fmt(total_pop, False)
            
            # Format inputs as plain numbers
            for_pop = fmt(for_pop, False)
            against_pop = fmt(against_pop, False)
            abstain_pop = fmt(abstain_pop, False)

    # Sum of Member States
    if for_num is not None and against_num is not None and abstain_num is not None and absent_num is not None:
        sum_num = int(for_num + against_num + abstain_num + absent_num)
    else:
        sum_num = ""
        
    def clean_int(val): return int(val) if val is not None else ""

    return {
        "status": "success",
        "voting_sheet_number": voting_sheet,
        "type_of_procedure": procedure,
        "number_of_draft": draft_number,
        "date_of_opinion": date_opinion,
        "consensus": consensus,
        
        "for_number": clean_int(for_num),
        "for_population": for_pop, 
        
        "against_number": clean_int(against_num),
        "against_population": against_pop,
        
        "abstain_number": clean_int(abstain_num),
        "abstain_population": abstain_pop,
        
        "absent_number": clean_int(absent_num),
        "absent_population": absent_pop,
        
        "sum_number": sum_num,
        "sum_population": sum_pop,
        "date_of_processing": datetime.now().strftime("%d-%m-%Y")
    }