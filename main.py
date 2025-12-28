from fastapi import FastAPI, UploadFile, File
import pdfplumber
import io
import re
from datetime import datetime

app = FastAPI()

def extract_number(pattern, text):
    
   
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        val_str = match.group(1).strip()
        
     
        if val_str in ["-", "–", "—"]:
            return 0.0
            
       
        val_str = val_str.replace(',', '.').replace('%', '')
        
        try:
            return float(val_str)
        except ValueError:
            return 0.0
    return 0.0

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
    
    vs_match = re.search(r"\b(V\d{3,8}\/\d{2})\b", text)
    if vs_match:
        voting_sheet = vs_match.group(1)
    else:
        
        voting_sheet = extract_text(r"(?:RegCom\s+)?(?:number\s+of\s+)?voting\s+sheet\s*[:\.]?\s*([A-Za-z0-9\-/]+)", text)

  
    procedure = extract_text(r"Type\s+of\s+procedure\s*[:\.]?\s*(.*)", text)
    draft_number = extract_text(r"(?:RegCom\s+)?Number\s+of\s+draft\s+implementing\s+act/measure\s*[:\.]?\s*(.*)", text)
    date_opinion = extract_text(r"Date\s+of\s+delivery\s+of\s+the\s+opinion\s*[:\.]?\s*(.*)", text)
    consensus = extract_text(r"Consensus\s*[:\.]?\s*(.*)", text)

   
    flat_text = text.replace('\n', ' ')

    # For (In favour)
    for_num = extract_number(r"Number of Member States in favour\s*:\s*([\d\-]+)", text)
    for_pop = extract_number(r"representing a population of\s*:\s*([\d,\.\-]+)\s*%", text)
    
   
    against_num = extract_number(r"Number of Member States against\s*:\s*([\d\-]+)", text)
    
    against_pop = extract_number(r"Number of Member States against.*?representing a population of\s*:\s*([\d,\.\-]+)\s*%", flat_text)
    
   
    abstain_num = extract_number(r"Number of abstentions\s*:\s*([\d\-]+)", text)
    abstain_pop = extract_number(r"Number of abstentions.*?representing a population of\s*:\s*([\d,\.\-]+)\s*%", flat_text)

  
    absent_num = extract_number(r"Number of Member States not represented\s*:\s*([\d\-]+)", text)

   
    absent_pop = 100.0 - (for_pop + against_pop + abstain_pop)
  
    if absent_pop < 0.01: absent_pop = 0.0
    
   
    sum_num = for_num + against_num + abstain_num + absent_num
    sum_pop = for_pop + against_pop + abstain_pop + absent_pop

    return {
        "status": "success",
        "voting_sheet_number": voting_sheet,
        "type_of_procedure": procedure,
        "number_of_draft": draft_number,
        "date_of_opinion": date_opinion,
        "consensus": consensus,
        "for_number": for_num,
        "for_population": f"{for_pop}%",
        "against_number": against_num,
        "against_population": f"{against_pop}%",
        "abstain_number": abstain_num,
        "abstain_population": f"{abstain_pop}%",
        "absent_number": absent_num,
        "absent_population": f"{absent_pop:.2f}%",  
        "sum_number": sum_num,
        "sum_population": f"{sum_pop:.2f}%",
        "date_of_processing": datetime.now().strftime("%d-%m-%Y")
    }