from fastapi import APIRouter
from core.auth import CurrentUser
from core.config import get_settings
from services.ai_budget import Budget

router=APIRouter(prefix='/ai',tags=['AI budget'])

@router.get('/usage')
def usage(user_id: CurrentUser):
    s=get_settings()
    return Budget(s).summary(user_id) | {'models':{
        'resume_drafting':s.groq_resume_model,'utility':s.groq_utility_model,
        'matching_and_verification':s.groq_model}}
