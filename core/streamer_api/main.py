from fastapi import FastAPI
from routes.core import router as core_router
from routes.ui import router as ui_router

app = FastAPI(title="RadioTiker Streamer API")
app.include_router(core_router)
app.include_router(ui_router)
