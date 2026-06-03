import argparse
import logging.config
import os
from pathlib import Path

from multi_t_q.config import settings
from multi_t_q.core import Multi_t_qService


def main() -> int:
    parser = argparse.ArgumentParser(description="multi_t_q service")
    parser.add_argument(
        "-d",
        "--execute-dir",
        type=Path,
        default=Path.cwd(),
        help="working directory for runtime files (logs, data, etc.)",
    )
    args = parser.parse_args()

    os.chdir(args.execute_dir)
    Path("logs").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)
    logging.config.dictConfig(settings.logs)

    Multi_t_qService(settings.service, args.execute_dir).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
