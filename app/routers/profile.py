from uuid import uuid4
from fastapi import APIRouter, Request, File, UploadFile, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from core.auth import CurrentUser
from core.supabase import user_client
from core.exceptions import not_found

router=APIRouter(prefix='/profile',tags=['profile'])


class ProfileUpdate(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra='forbid')
    display_name: str=Field(min_length=1,max_length=100)


def profile_with_avatar(database, user_id):
    rows=database.table('profiles').select('*').eq('id',user_id).execute().data
    if not rows:raise not_found('Profile')
    row=rows[0]
    if row.get('avatar_path'):
        result=database.storage.from_('avatars').create_signed_url(row['avatar_path'],3600)
        row['avatar_url']=result.get('signedURL') or result.get('signedUrl')
    else:row['avatar_url']=None
    return row


@router.get('')
def get_profile(request:Request,user_id:CurrentUser):
    return profile_with_avatar(user_client(request.state.access_token),user_id)


@router.patch('')
def update_profile(payload:ProfileUpdate,request:Request,user_id:CurrentUser):
    database=user_client(request.state.access_token)
    database.table('profiles').update(payload.model_dump()).eq('id',user_id).execute()
    return profile_with_avatar(database,user_id)


@router.post('/avatar')
def upload_avatar(request:Request,user_id:CurrentUser,file:UploadFile=File(...)):
    content=file.file.read(2*1024*1024+1)
    valid={'image/png':content.startswith(b'\x89PNG\r\n\x1a\n'),
           'image/jpeg':content.startswith(b'\xff\xd8\xff'),
           'image/webp':content.startswith(b'RIFF') and content[8:12]==b'WEBP'}
    if not content or not valid.get(file.content_type):raise HTTPException(415,'Choose a PNG, JPEG, or WebP image')
    if len(content)>2*1024*1024:raise HTTPException(413,'Profile images must be under 2 MB')
    database=user_client(request.state.access_token)
    old=profile_with_avatar(database,user_id).get('avatar_path')
    ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[file.content_type]
    path=f'{user_id}/{uuid4()}.{ext}'
    bucket=database.storage.from_('avatars')
    bucket.upload(path,content,{'content-type':file.content_type})
    try:database.table('profiles').update({'avatar_path':path}).eq('id',user_id).execute()
    except Exception:
        bucket.remove([path]);raise
    if old:
        # Failure to clean an old object must not roll back the new saved avatar.
        try:bucket.remove([old])
        except Exception:pass
    return profile_with_avatar(database,user_id)
