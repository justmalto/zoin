# app/routers/applications.py

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status, BackgroundTasks

from core.auth import CurrentUser
from services.mail_sync import sync_account, configured
from core.exceptions import not_found
from core.supabase import user_client
from schemas.application import (
    ApplicationCreate,
    ApplicationUpdate,
    StatusTransition,
)

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    request: Request,
    tasks: BackgroundTasks,
    user_id: CurrentUser = None,
):
    result = user_client(request.state.access_token).rpc(
        "create_application", {"p_payload": payload.model_dump(mode="json")}
    ).execute()
    row = result.data[0] if isinstance(result.data, list) else result.data
    if configured():
        tasks.add_task(sync_account, user_id, "job_saved", row["id"])
    return row


@router.get("")
def list_applications(
    request: Request,
    user_id: CurrentUser = None,
):
    database = user_client(request.state.access_token)
    rows = database.table("applications").select("*").eq("user_id", user_id).order("updated_at", desc=True).execute().data
    resumes = database.table("resumes").select("id,file_name").eq("user_id", user_id).execute().data
    names = {r["id"]: r["file_name"] for r in resumes}
    recommendations = database.table("resume_recommendations").select("application_id,resume_id,match_score").eq("user_id", user_id).execute().data
    scores = {(r["application_id"],r["resume_id"]): r["match_score"] for r in recommendations}
    return [row | {"resume_name": names.get(row["selected_resume_id"]),
                   "match_score": scores.get((row["id"],row["selected_resume_id"]))} for row in rows]


@router.get("/{application_id}")
def get_application(
    application_id: UUID,
    request: Request,
    user_id: CurrentUser = None,
):
    result = (
        user_client(request.state.access_token)
        .table("applications")
        .select("*")
        .eq("id", str(application_id))
        .eq("user_id", user_id)
        .execute()
    )

    if not result.data:
        raise not_found("Application")

    return result.data[0]


@router.patch("/{application_id}")
def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    request: Request,
    tasks: BackgroundTasks,
    user_id: CurrentUser = None,
):
    changes = payload.model_dump(
        exclude_unset=True,
        mode="json",
    )

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No application changes supplied",
        )

    result = (
        user_client(request.state.access_token)
        .table("applications")
        .update(changes)
        .eq("id", str(application_id))
        .eq("user_id", user_id)
        .execute()
    )

    if not result.data:
        raise not_found("Application")

    row = result.data[0]
    if row["status"] == "found" and configured():
        tasks.add_task(sync_account, user_id, "job_saved", row["id"])
    return row


@router.post("/{application_id}/transition")
def transition_application(
    application_id: UUID,
    payload: StatusTransition,
    request: Request,
    user_id: CurrentUser = None,
):
    try:
        result = (
            user_client(request.state.access_token)
            .rpc(
                "transition_application",
                {
                    "p_application_id": str(application_id),
                    "p_to_status": payload.to_status,
                    "p_note": payload.note,
                    "p_source": "user",
                },
            )
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid lifecycle transition",
        ) from exc

    if not result.data:
        raise not_found("Application")

    return result.data


@router.get("/{application_id}/timeline")
def get_application_timeline(
    application_id: UUID,
    request: Request,
    user_id: CurrentUser = None,
):
    database = user_client(request.state.access_token)

    application = (
        database.table("applications")
        .select("id")
        .eq("id", str(application_id))
        .eq("user_id", user_id)
        .execute()
    )

    if not application.data:
        raise not_found("Application")

    return (
        database.table("application_events")
        .select("*")
        .eq("application_id", str(application_id))
        .eq("user_id", user_id)
        .order("happened_at")
        .execute()
        .data
    )

@router.post('/{application_id}/receipt-correction')
def correct_receipt(application_id:UUID,request:Request,user_id:CurrentUser):
    result=user_client(request.state.access_token).rpc('correct_application_receipt',{'p_application_id':str(application_id)}).execute().data
    return result[0] if isinstance(result,list) else result
