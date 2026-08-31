from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from api.routes import router
from config.settings import settings

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "static"

app = FastAPI(title="Samsung Phone Query and Review System", version="1.0.0")
app.include_router(router, prefix="/api")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
