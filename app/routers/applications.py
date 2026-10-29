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
    followup_preferences = {}
    questions_by_application = {}
    agent_by_application = {}
    from core.config import get_settings
    if get_settings().discovery_enabled:
        from services.discovery_store import latest
        followup_preferences=latest(user_id)['preferences'] or {}
        questions_by_application={r['application_id']:r for r in database.table('application_agent_questions').select('application_id,state,questions').eq('user_id',user_id).execute().data}
        from services.agent_applications import attempts
        agent_by_application = {a['application_id']: a for a in attempts(user_id) if a.get('application_id')}
    draft_by_application={}
    if get_settings().discovery_enabled:
        from services.automation import activity
        draft_by_application={d['application_id']:d for d in activity(user_id)['drafts'] if d.get('application_id') and d['state']!='discarded'}
    from services.application_questions import followup_label
    return [row | {"found_followup":followup_label(row,followup_preferences,agent_by_application.get(row["id"])),"agent_questions": {"state":questions_by_application[row["id"]]["state"],"pending":sum(bool(q.get("required") and not q.get("answer")) for q in questions_by_application[row["id"]]["questions"])} if row["id"] in questions_by_application else None,"tailored_resume":draft_by_application.get(row['id']),"agent_submission": agent_by_application.get(row['id']), "resume_name": names.get(row["selected_resume_id"]),
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

    row=result.data[0]
    from core.config import get_settings
    if get_settings().discovery_enabled:
        from services.automation import activity
        row=row|{'agent_questions_enabled':True,'tailored_resume':next((d for d in activity(user_id)['drafts'] if d.get('application_id')==str(application_id) and d['state']!='discarded'),None)}
    return row


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

    events = (
        database.table("application_events")
        .select("*")
        .eq("application_id", str(application_id))
        .eq("user_id", user_id)
        .order("happened_at")
        .execute()
        .data
    )
    from core.config import get_settings
    if get_settings().discovery_enabled:
        from services.agent_applications import attempts
        events += [{'id':a['id'],'application_id':str(application_id),'event_type':'agent_submission',
                    'source':'agent','from_status':None,'to_status':None,'note':a['note'],
                    'happened_at':a['updated_at']} for a in attempts(user_id) if a.get('application_id')==str(application_id)]
    return sorted(events,key=lambda event:event['happened_at'])

@router.post('/{application_id}/receipt-correction')
def correct_receipt(application_id:UUID,request:Request,user_id:CurrentUser):
    result=user_client(request.state.access_token).rpc('correct_application_receipt',{'p_application_id':str(application_id)}).execute().data
    return result[0] if isinstance(result,list) else result
