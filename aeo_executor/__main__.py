"""AEO Executor — Phone Automation Service entry point."""

import uvicorn
from fastapi import FastAPI

from .api.routes import router
from .config import HOST, PORT

app = FastAPI(
    title="AEO Executor",
    description="Phone automation service for AEO daily sessions and ranking audits",
    version="1.0.0",
)

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("aeo_executor.__main__:app", host=HOST, port=PORT, reload=True)
