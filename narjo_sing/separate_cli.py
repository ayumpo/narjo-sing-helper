from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .separation import detect_device, prepare_model, separate_vocals_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m narjo_sing.separate_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("device")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--model", required=True)
    prepare.add_argument("--models-dir", required=True, type=Path)
    separate = sub.add_parser("separate")
    separate.add_argument("--model", required=True)
    separate.add_argument("--input", required=True, type=Path)
    separate.add_argument("--output", required=True, type=Path)
    separate.add_argument("--models-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "device":
        print(detect_device())
    elif args.command == "prepare":
        prepare_model(args.model, args.models_dir)
    else:
        separate_vocals_file(args.model, args.input, args.output, args.models_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
