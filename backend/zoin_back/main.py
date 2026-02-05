from fastapi import FastAPI
from api.auth import router as auth_router

app=FastAPI("Zoin_Backend")

app.include_router(auth_router)