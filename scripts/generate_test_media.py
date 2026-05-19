#!/usr/bin/env python3
import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    destination = Path(args.destination)
    destination.mkdir(parents=True, exist_ok=True)
    for index in range(args.count):
        (destination / f"sample-{index}.txt").write_text(f"sample file {index}\n")


if __name__ == "__main__":
    main()
