"""Local CLI: no test user, database upload, or Gmail access is required."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from services.groq_client import AIError
from services.job_extractor import extract_job
from services.recommendation_service import match_resume
from services.resume_parser import extract_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', type=Path, required=True)
    parser.add_argument('--jd', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--extract-job-only', action='store_true')
    args = parser.parse_args()
    jd = args.jd.read_text()
    try:
        if args.extract_job_only:
            result = extract_job(jd)
        else:
            if args.resume.stat().st_size > 10 * 1024 * 1024:
                raise ValueError('Resume exceeds 10 MB')
            mime = {'.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.txt': 'text/plain'}.get(args.resume.suffix.lower())
            if not mime:
                raise ValueError('Only PDF, DOCX, and TXT resumes are supported')
            text = extract_text(args.resume.read_bytes(), mime, args.resume.name)
            result = match_resume(jd, text) | {'resume_file': args.resume.name, 'job_source': args.jd.name}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
        print(f'Saved verified analysis to {args.output}')
        return 0
    except (AIError, ValueError, OSError) as exc:
        print(f'Analysis unavailable: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
