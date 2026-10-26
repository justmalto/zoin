# app/routers/recommendations.py

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from core.auth import CurrentUser
from core.exceptions import not_found
from core.supabase import user_client
from services.recommendation_service import generate_recommendations

router = APIRouter(
    prefix="/applications",
    tags=["recommendations"],
)


@router.post(
    "/{application_id}/recommendations",
    status_code=status.HTTP_201_CREATED,
)
def generate_resume_recommendations(
    application_id: UUID,
    request: Request,
    user_id: CurrentUser = None,
    resume_id: UUID | None = None,
):
    database = user_client(request.state.access_token)

    application = (
        database.table("applications")
        .select("*")
        .eq("id", str(application_id))
        .eq("user_id", user_id)
        .execute()
    )

    if not application.data:
        raise not_found("Application")

    # Bound each call to one resume so the free-tier token budget can be
    # respected. Compare additional resumes through subsequent requests.
    requested_resume = resume_id or application.data[0].get("selected_resume_id")
    query = database.table("resumes").select("id,file_name,resume_text,content_hash").eq("user_id", user_id)
    if requested_resume:
        query = query.eq("id", str(requested_resume))
    else:
        query = query.order("is_active", desc=True).order("created_at", desc=True)
    resumes = query.limit(1).execute()

    if not resumes.data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Requested resumé is unavailable or no resumé has been uploaded",
        )

    recommendations = generate_recommendations(
        application=application.data[0],
        resumes=resumes.data,
    )

    # The database locks both inputs and rejects results if either changed
    # while Groq was running, before any stale score can be persisted.
    return database.rpc('store_resume_recommendation', {
        'p_application_id': str(application_id),
        'p_resume_id': resumes.data[0]['id'],
        'p_expected_jd': application.data[0]['job_description'],
        'p_expected_hash': resumes.data[0]['content_hash'],
        'p_analysis': recommendations[0],
    }).execute().data



@router.get("/{application_id}/recommendations")
def list_resume_recommendations(
    application_id: UUID,
    request: Request,
    user_id: CurrentUser = None,
):
    return (
        user_client(request.state.access_token)
        .table("resume_recommendations")
        .select("*, resumes(file_name)")
        .eq("application_id", str(application_id))
        .eq("user_id", user_id)
        .order("match_score", desc=True)
        .execute()
        .data
    )