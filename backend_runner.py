import os

import uvicorn

from app.main import app


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("SSO_BRIDGE_PORT", "8765")),
    )
