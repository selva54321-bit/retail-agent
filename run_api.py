from __future__ import annotations

import uvicorn

from api.config import settings


if __name__ == "__main__":
    uvicorn.run("api.main:app", host=settings.host, port=settings.port, reload=False)
 