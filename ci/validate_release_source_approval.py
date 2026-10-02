#!/usr/bin/env python3
"""Validate protected release pins and export them to the resolver job."""
import os
from pathlib import Path

from bootstrap import validate_release_source_approval


def main():
    try:
        approval = validate_release_source_approval(
            os.environ, os.environ.get('REQUESTED_SOURCE_SHA', ''),
            os.environ.get('REQUESTED_BUILDER_SHA', ''))
        output_path = Path(os.environ.get('GITHUB_OUTPUT', ''))
        if output_path.is_symlink() or not output_path.is_file():
            raise ValueError('The workflow output file is unavailable')
        with output_path.open('a', encoding='ascii', newline='\n') as output:
            output.write(f"source_sha={approval['source_sha']}\n")
            output.write(f"builder_sha={approval['builder_sha']}\n")
    except Exception:
        print('Protected release-source-approval pins are missing or do not match this run.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
