"""Local résumé-first shortlist. Reads files locally; never submits applications.

Example: .venv/bin/python scripts/discover_jobs.py --resume resume.pdf --resume sde.docx
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from schemas.discovery import DiscoveryPreferences
from services.job_discovery import profile_resumes, discover
from services.groq_client import AIError
from services.discovery_store import ai_cache


def read_file(path):
    if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('Each resume must be an existing file no larger than 10 MB.')
    if path.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if len(reader.pages) > 20: raise ValueError('Use a resume with at most 20 pages.')
        return '\n'.join(page.extract_text() or '' for page in reader.pages)[:18000]
    if path.suffix.lower() == '.docx':
        from docx import Document
        return '\n'.join(p.text for p in Document(path).paragraphs)[:18000]
    if path.suffix.lower() == '.txt': return path.read_text()[:18000]
    raise ValueError('Use PDF, DOCX or TXT resumes.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', type=Path, action='append', required=True)
    parser.add_argument('--location', action='append', help='Repeat for multiple locations; default India.')
    parser.add_argument('--max-required-years', type=int)
    parser.add_argument('--include-senior-roles', action='store_true')
    parser.add_argument('--no-worldwide-remote', action='store_true')
    args = parser.parse_args()
    if len(args.resume) > 10: parser.error('At most 10 resumes are supported.')
    try:
        resumes = [{'id': hashlib.sha256(str(p.resolve()).encode()).hexdigest()[:16], 'file_name':p.name, 'resume_text':read_file(p)} for p in args.resume]
        owner='cli:'+hashlib.sha256('|'.join(sorted(str(p.resolve()) for p in args.resume)).encode()).hexdigest()
        cache=lambda action,key,value:ai_cache(owner,action,key,value)
        profiles = profile_resumes(resumes,cache=cache)
        if not profiles: parser.error('No evidence-supported career profile could be identified from the text.')
        prefs = DiscoveryPreferences(locations=args.location or ['India'], max_required_years=args.max_required_years, worldwide_remote=not args.no_worldwide_remote, include_senior_roles=args.include_senior_roles).model_dump()
        print(json.dumps(discover(profiles, prefs, resumes=resumes,cache=cache), indent=2, ensure_ascii=False))
    except AIError as exc:
        print(json.dumps({'error':str(exc),'retry_after':exc.retry_after,'completed_ai_stages_cached':True}),file=sys.stderr)
        raise SystemExit(2)
    except ValueError as exc: parser.error(str(exc))

if __name__ == '__main__': main()
