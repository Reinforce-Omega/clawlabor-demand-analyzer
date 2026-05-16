import asyncio
import os

from dotenv import load_dotenv

from src.config import load_config
from src.logging_config import configure_logging
from src.worker import Worker


def main() -> None:
    load_dotenv()
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))

    config = load_config()
    asyncio.run(Worker(config).run())


if __name__ == "__main__":
    main()
