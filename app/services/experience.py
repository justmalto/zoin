"""Conservative month-level bounds from an explicit Experience section only."""
import re
from datetime import date

MONTHS = {name: i for i, name in enumerate(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'],1)}
MONTH = r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
RANGE = re.compile(MONTH+r'\s*(\d{4})\s*[-–—]\s*(?:'+MONTH+r'\s*(\d{4})|(present|current|now))',re.I)


def check_experience(job_text: str, resume_text: str, as_of: str) -> dict:
    today = date.fromisoformat(as_of)
    years = re.findall(r'(\d+(?:\.\d+)?)(?:\s*(?:-|–|to)\s*\d+(?:\.\d+)?)?\s*\+?\s*(?:years?|yrs?)\b',job_text,re.I)
    required = min(float(y) for y in years) if years else None
    match = re.search(r'(?im)^\s*(?:work\s+|professional\s+)?experience\s*$',resume_text)
    if not match:
        return {'required_years':required,'documented_months_upper_bound':None,'method':'No explicit Experience section with supported month/year date ranges was found.'}
    section = resume_text[match.end():]
    section = re.split(r'(?im)^\s*(?:projects|education|publications|certifications|skills)\s*$',section)[0]
    months = set();spans=[]
    current = today.year*12+today.month-1
    for m in RANGE.finditer(section):
        first,year,last,end_year,present = m.groups()
        start=int(year)*12+MONTHS[first[:3].lower()]-1
        end=current if present else int(end_year)*12+MONTHS[last[:3].lower()]-1
        if start>end or start>current:
            continue
        end=min(end,current)
        months.update(range(start,end+1))
        spans.append(m.group(0))
    upper = len(months) if spans else None
    return {'required_years':required,'documented_months_upper_bound':upper,'date_spans':spans,
            'method':'Union of calendar months in dated Experience entries, including internships and partial endpoint months. This is an upper bound, not verified full-time ML tenure.'}
