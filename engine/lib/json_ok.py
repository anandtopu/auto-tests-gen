#!/usr/bin/env python3
"""Exit 0 if the named file parses as JSON, non-zero otherwise.

A one-line guard that exists as a file rather than an inline `python3 -c`
because pipeline.sh is CRLF and quoting a nested python one-liner through
bash on Windows is how the last three attempts at this broke.

Used by pipeline.sh's write_run_record: the record is produced to scratch,
verified here, and only then moved into place — so a producer that dies
mid-stream cannot leave a truncated file where the durable record belongs.
"""
import json
import sys


def main(argv):
    if len(argv) != 2:
        print("usage: json_ok.py <file>", file=sys.stderr)
        return 64
    try:
        with open(argv[1], encoding="utf-8") as fh:
            json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"{argv[1]}: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
