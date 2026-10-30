"""Evidence-backed resume matching with a transparent weighted rubric."""
import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.config import get_settings
from schemas.ai import ResumeAssessment
from services.experience import check_experience
from services.groq_client import AIError, complete_json, cached_result
from services.job_extractor import UNTRUSTED_RULE

WEIGHTS = {"ml_modeling": 20, "data_preparation": 15, "validation": 15, "mlops_api_cloud": 20,
           "business_domain": 10, "experience": 10, "education": 5, "communication": 5}
FACTORS = {"met": 1, "partial": .5, "not_evidenced": 0, "not_required": 0}


@cached_result('resume_match')
def match_resume(job_description: str, resume_text: str) -> dict:
    if not 20 <= len(job_description) <= 18000 or not 20 <= len(resume_text) <= 18000:
        raise AIError("Job and resume text must each contain 20 to 18000 characters", 422)
    # The model selects source IDs, never rewrites quotations. This avoids
    # treating paraphrases or PDF typography changes as original evidence.
    def passages(text: str, prefix: str) -> dict[str, str]:
        lines = [line.strip() for line in re.split(r"\n+", text) if line.strip()]
        return {f"{prefix}{i:03d}": line for i, line in enumerate(lines, 1)}
    job_sources = passages(job_description, "J")
    resume_sources = passages(resume_text, "R")
    as_of = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    prompt = UNTRUSTED_RULE + """Compare resume to JD for career-development advice, not hiring decisions.
Evaluate each category exactly once: ml_modeling,data_preparation,validation,mlops_api_cloud,business_domain,experience,education,communication.
Use met/partial/not_evidenced/not_required. Missing evidence does not mean missing ability.
Use not_required only when the JD has no requirement for that category, with requirement_id=null.
Otherwise requirement_id must be an existing J ID. Every met/partial criterion and every strength
needs existing R evidence IDs that support its claim. IDs only in ID fields, never paraphrased quotes.
Cover all relevant requirements per category, not just the easiest. Listed tools/projects do not prove
production experience. Distinguish projects/internships from employment and related from exact degrees.
Use only explicit employment dates and experience_date_check for tenure; no graduation dates or gaps.
If documented months are below required years, experience cannot be met. Code computes the score.
Give concise explanations and actionable, truthful suggestions; never invent metrics or experience.
"""
    experience_check = check_experience(job_description, resume_text, as_of)
    output = complete_json(prompt, json.dumps({
        "as_of": as_of, "job_passages": job_sources, "resume_passages": resume_sources, "experience_date_check": experience_check,
    }, ensure_ascii=False, separators=(",", ":")), ResumeAssessment, max_output_tokens=2600)
    adjustments = []
    minimum = experience_check.get("required_years")
    months = experience_check.get("documented_months_upper_bound")
    if minimum is not None:
        for item in output.criteria:
            if item.category != "experience":
                continue
            if months is not None and months < minimum * 12:
                item.coverage = "partial" if months and item.resume_evidence_ids else "not_evidenced"
                item.explanation = (f"The dated Experience entries cover at most {months} calendar months, "
                    f"including internships and partial months, below the stated {minimum:g}+ years. "
                    "The required duration of building/deploying ML models is not established.")
                adjustments.append("Experience coverage bounded by dated employment, not the model's arithmetic.")
            elif months is None and item.coverage in {"met", "not_required"}:
                item.coverage = "not_evidenced"
                item.explanation = "Supported employment date ranges were not found, so the required duration could not be verified."
                adjustments.append("Required experience duration could not be verified from supported dates.")
    categories = [item.category for item in output.criteria]
    if len(categories) != len(WEIGHTS) or set(categories) != set(WEIGHTS):
        raise AIError("Analysis did not cover every rubric category exactly once")
    earned = 0.0
    denominator = 0
    criteria = []
    for item in output.criteria:
        if item.coverage == "not_required":
            if item.requirement_id is not None:
                raise AIError("Unexpected requirement in a non-applicable criterion")
        else:
            if not item.requirement_id:
                raise AIError("Analysis omitted a job requirement quote")
            if item.requirement_id not in job_sources:
                raise AIError("Analysis referenced an unknown job passage")
            denominator += WEIGHTS[item.category]
            earned += WEIGHTS[item.category] * FACTORS[item.coverage]
        if item.coverage in {"met", "partial"} and not item.resume_evidence_ids:
            raise AIError("Analysis claimed a match without resume evidence")
        for source_id in item.resume_evidence_ids:
            if source_id not in resume_sources:
                raise AIError("Analysis referenced an unknown resume passage")
        criteria.append({"category": item.category, "coverage": item.coverage,
                         "requirement_quote": job_sources.get(item.requirement_id),
                         "resume_evidence": [resume_sources[i] for i in item.resume_evidence_ids],
                         "explanation": item.explanation, "weight": WEIGHTS[item.category],
                         "requirement_source_id": item.requirement_id,
                         "resume_source_ids": item.resume_evidence_ids})
    if denominator == 0:
        raise AIError("No relevant job requirements were identified")
    for strength in output.strengths:
        if not strength.evidence_ids:
            raise AIError("Analysis claimed a strength without evidence")
        for source_id in strength.evidence_ids:
            if source_id not in resume_sources:
                raise AIError("Strength referenced an unknown resume passage")
    gaps = [{"requirement": job_sources[item.requirement_id], "severity": "high" if item.category in {"experience", "mlops_api_cloud"} else "medium", "explanation": item.explanation}
            for item in output.criteria if item.coverage in {"partial", "not_evidenced"}]
    return {"match_score": round(100 * earned / denominator, 1), "summary": output.summary,
            "strengths": [{"claim": s.claim, "evidence": [resume_sources[i] for i in s.evidence_ids]} for s in output.strengths], "gaps": gaps,
            "suggestions": output.suggestions, "criteria": criteria, "rubric_version": "v1", "as_of": as_of,
            "score_note": "Weighted coverage of documented requirements; not a hiring probability or validated ATS score.",
            "model_name": get_settings().groq_model, "experience_check": experience_check, "validation_adjustments": adjustments}


def generate_recommendations(application: dict[str, Any], resumes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # One resume per request keeps free-tier prompts bounded. Partial results are
    # not persisted by the route if a later comparison hits provider limits.
    return sorted([match_resume(application["job_description"], resume["resume_text"]) | {"resume_id": resume["id"]}
                   for resume in resumes], key=lambda item: item["match_score"], reverse=True)
