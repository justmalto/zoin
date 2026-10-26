# app/routers/resumes.py

import hashlib
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from core.auth import CurrentUser
from core.config import get_settings
from core.exceptions import conflict, not_found
from core.supabase import user_client
from services.resume_parser import chunk_text
from services.resume_upload import read_resume
from postgrest.exceptions import APIError

router = APIRouter(prefix="/resumes", tags=["resumes"])

ALLOWED_FILE_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}


@router.post("", status_code=status.HTTP_201_CREATED)
def upload_resume(
    request: Request,
    file: UploadFile = File(...),
    user_id: CurrentUser = None,
    replace_id: UUID | None = None,
):
    file_bytes, resume_text = read_resume(file)
    file_name = file.filename or 'resume'
    database = user_client(request.state.access_token)
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    duplicate = (
        database.table("resumes")
        .select("id")
        .eq("user_id", user_id)
        .eq("content_hash", content_hash)
        .execute()
    )

    if duplicate.data and replace_id and str(duplicate.data[0]['id']) != str(replace_id):
        raise HTTPException(409, {'message':'This file is already saved in another library entry. Select that existing resume instead of replacing.'})

    if duplicate.data:
        raise HTTPException(409, {"message": "This resumé has already been uploaded", "resume_id": duplicate.data[0]["id"]})

    previous = None
    if replace_id:
        rows=database.table('resumes').select('id,file_path').eq('id',str(replace_id)).eq('user_id',user_id).execute().data
        if not rows:raise not_found('Resume to replace')
        previous=rows[0]
    else:
        count=database.table('resumes').select('id').eq('user_id',user_id).execute().data
        if len(count)>=10:raise HTTPException(409,{'code':'resume_limit','message':'You have 10 saved resumes. Choose one to replace.','limit':10})

    active_resume = (
        database.table("resumes")
        .select("id")
        .eq("user_id", user_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    safe_file_name = re.sub(r"[^A-Za-z0-9._-]", "_", file_name)
    storage_path = f"{user_id}/{uuid4()}/{safe_file_name}"
    uploaded = False
    created_id = None

    try:
        database.storage.from_(
            get_settings().supabase_resume_bucket
        ).upload(
            storage_path,
            file_bytes,
            {
                "content-type": file.content_type,
                "upsert": "false",
            },
        )

        uploaded = True
        if previous:
            payload={'file_name':file_name,'file_path':storage_path,'mime_type':file.content_type,'content_hash':content_hash,'resume_text':resume_text}
            updated=database.rpc('replace_resume',{'p_resume_id':str(replace_id),'p_payload':payload,'p_chunks':chunk_text(resume_text)}).execute().data
            try:database.storage.from_(get_settings().supabase_resume_bucket).remove([previous['file_path']])
            except Exception:pass
            return updated[0] if isinstance(updated,list) else updated
        result = (
            database.table("resumes")
            .insert(
                {
                    "user_id": user_id,
                    "file_name": file_name,
                    "file_path": storage_path,
                    "mime_type": file.content_type,
                    "content_hash": content_hash,
                    "resume_text": resume_text,
                    "is_active": False,
                }
            )
            .execute()
        )

        resume = result.data[0]
        created_id = resume["id"]

        chunks = [
            chunk | {"resume_id": resume["id"], "user_id": user_id}
            for chunk in chunk_text(resume_text)
        ]

        database.table("resume_chunks").insert(chunks).execute()

        if not active_resume.data:
            database.rpc("activate_resume", {"p_resume_id": created_id}).execute()
            resume["is_active"] = True
        return resume

    except Exception as exc:
        # Compensate only objects created by this request; random paths avoid
        # deleting another concurrent upload of the same content.
        if created_id:
            try: database.table("resumes").delete().eq("id", created_id).eq("user_id", user_id).execute()
            except Exception: pass
        if uploaded:
            try: database.storage.from_(get_settings().supabase_resume_bucket).remove([storage_path])
            except Exception: pass
        if isinstance(exc,APIError) and 'Saved resume limit reached' in str(exc):
            raise HTTPException(409,{'code':'resume_limit','message':'You have 10 saved resumes. Choose one to replace.','limit':10}) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to store the resumé",
        ) from exc


@router.get("")
def list_resumes(
    request: Request,
    user_id: CurrentUser = None,
):
    return (
        user_client(request.state.access_token)
        .table("resumes")
        .select("id,file_name,mime_type,is_active,created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )


@router.get("/{resume_id}")
def get_resume(
    resume_id: UUID,
    request: Request,
    user_id: CurrentUser = None,
):
    result = (
        user_client(request.state.access_token)
        .table("resumes")
        .select("*")
        .eq("id", str(resume_id))
        .eq("user_id", user_id)
        .execute()
    )

    if not result.data:
        raise not_found("Resumé")

    return result.data[0]


@router.get("/{resume_id}/chunks")
def get_resume_chunks(
    resume_id: UUID,
    request: Request,
    user_id: CurrentUser = None,
):
    return (
        user_client(request.state.access_token)
        .table("resume_chunks")
        .select("*")
        .eq("resume_id", str(resume_id))
        .eq("user_id", user_id)
        .order("chunk_index")
        .execute()
        .data
    )


@router.post("/{resume_id}/activate")
def activate_resume(
    resume_id: UUID,
    request: Request,
    user_id: CurrentUser = None,
):
    result = (
        user_client(request.state.access_token)
        .rpc("activate_resume", {"p_resume_id": str(resume_id)})
        .execute()
    )

    if not result.data:
        raise not_found("Resumé")

    return result.data


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: UUID,
    request: Request,
    user_id: CurrentUser = None,
):
    database = user_client(request.state.access_token)

    resume = (
        database.table("resumes")
        .select("file_path")
        .eq("id", str(resume_id))
        .eq("user_id", user_id)
        .execute()
    )

    if not resume.data:
        raise not_found("Resumé")

    database.storage.from_(
        get_settings().supabase_resume_bucket
    ).remove([resume.data[0]["file_path"]])

    database.table("resumes").delete().eq(
        "id",
        str(resume_id),
    ).eq(
        "user_id",
        user_id,
    ).execute()

@router.get("/{resume_id}/download")
def download_resume(resume_id: UUID, request: Request, user_id: CurrentUser):
    database = user_client(request.state.access_token)
    row = database.table("resumes").select("file_path,file_name").eq("id", str(resume_id)).eq("user_id", user_id).execute().data
    if not row: raise not_found("Resume")
    result = database.storage.from_(get_settings().supabase_resume_bucket).create_signed_url(row[0]["file_path"], 60)
    return {"url": result.get("signedURL") or result.get("signedUrl"), "file_name": row[0]["file_name"]}
