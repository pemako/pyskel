import argparse
import logging.config
import os
from pathlib import Path

from simple.config import settings
from simple.core import SimpleService


def main() -> int:
    parser = argparse.ArgumentParser(description="simple service")
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

    SimpleService(settings.service, args.execute_dir).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
