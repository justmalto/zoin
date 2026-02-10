from fastapi import FastAPI
from api.auth import router as auth_router
from fastapi.middleware.cors import CORSMiddleware

app=FastAPI(title="Zoin_Backend")


# CORS (REQUIRED for frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)