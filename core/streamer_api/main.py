from fastapi import FastAPI

# Use absolute package imports so uvicorn can resolve the module reliably.
from streamer_api.routes.core import router as core_router
from streamer_api.routes.ui import router as ui_router

app = FastAPI(title="RadioTiker Streamer API")
app.include_router(core_router)
app.include_router(ui_router)
