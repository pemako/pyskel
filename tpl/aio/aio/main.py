import argparse
import asyncio
import logging.config
import os
from pathlib import Path

from aio.config import settings
from aio.core import AioService


def main() -> int:
    parser = argparse.ArgumentParser(description="aio service")
    parser.add_argument(
        "-d",
        "--execute-dir",
        type=Path,
        default=Path.cwd(),
        help="working directory for runtime files (logs, etc.)",
    )
    args = parser.parse_args()

    os.chdir(args.execute_dir)
    Path("logs").mkdir(exist_ok=True)
    logging.config.dictConfig(settings.logs)

    asyncio.run(AioService(settings.service, args.execute_dir).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
