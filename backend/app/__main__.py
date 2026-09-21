import uvicorn

from .api import create_app
from .config import load_settings

settings = load_settings()
uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)
