import asyncio
import sys

import uvicorn

from novel_writer.core.config import get_settings


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    settings = get_settings()
    uvicorn.run("novel_writer.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
