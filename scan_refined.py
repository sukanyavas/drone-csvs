#!/usr/bin/env python3
"""Run the refined SEC drone-keyword screen."""

import sys

import scan_filings


if __name__ == "__main__":
    sys.exit(scan_filings.main(sys.argv[1:]))
