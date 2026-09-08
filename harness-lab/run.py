"""Start from the extracted harness-lab folder: uv run run.py --help."""
import sys
from harness_lab.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("--help")
    main()
