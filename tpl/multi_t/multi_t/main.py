import argparse
import logging.config
import os
from pathlib import Path

from multi_t.config import settings
from multi_t.core import Multi_tService


def main() -> int:
    parser = argparse.ArgumentParser(description="multi_t service")
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

    Multi_tService(settings.service, args.execute_dir).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
