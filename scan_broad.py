#!/usr/bin/env python3
"""Run the original broad SEC drone-keyword screen."""

import sys

import scan_filings


if __name__ == "__main__":
    sys.exit(scan_filings.main(["--broad", *sys.argv[1:]]))
