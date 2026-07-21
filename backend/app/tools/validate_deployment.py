from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.deployment import validate_self_hosting_settings


def main() -> int:
    validation = validate_self_hosting_settings(get_settings())

    if validation.ok:
        print("deployment configuration: ok")
    else:
        print("deployment configuration: failed")

    for check in validation.errors:
        print(f"ERROR {check.key}: {check.message}")
    for check in validation.warnings:
        print(f"WARNING {check.key}: {check.message}")

    return 0 if validation.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
