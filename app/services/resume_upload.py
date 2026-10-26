"""Request-scoped resume reading shared by preview and permanent uploads."""
from fastapi import HTTPException
from services.resume_parser import extract_text

ALLOWED_FILE_TYPES={'application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document','text/plain'}

def read_resume(file):
    if file.content_type not in ALLOWED_FILE_TYPES:
        raise HTTPException(415,'Only PDF, DOCX and TXT resumes are supported')
    content=file.file.read(10*1024*1024+1)
    if not content:raise HTTPException(400,'The uploaded file is empty')
    if len(content)>10*1024*1024:raise HTTPException(413,'Maximum upload size is 10 MB. Choose a smaller file.')
    try:text=extract_text(content,file.content_type,file.filename or 'resume')
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except Exception as exc:raise HTTPException(422,'This file could not be read. Upload a valid PDF, DOCX or TXT.') from exc
    if not text.strip():raise HTTPException(422,'No readable text was found in this resume')
    return content,text
