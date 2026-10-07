from fastapi import FastAPI

from app.db import Base, engine
from app.routers import files

Base.metadata.create_all(engine)

app = FastAPI(title="Geospatial File Measurement API", version="1.0.0")
app.include_router(files.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
