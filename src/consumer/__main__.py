"""`python -m consumer <api_base_url> <series_id>`: prints the feature table as JSON, or refuses."""

from __future__ import annotations

import json
import sys

from consumer.client import Client, ConsumerError, run
from consumer.contract import ContractViolation, IntegrityError


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m consumer <api_base_url> <series_id>", file=sys.stderr)
        return 2
    try:
        result = run(Client(argv[0]), argv[1])
    except ContractViolation as exc:
        print(f"REFUSED (contract violation): {exc}", file=sys.stderr)
        return 3
    except IntegrityError as exc:
        print(f"REFUSED (integrity): {exc}", file=sys.stderr)
        return 4
    except ConsumerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
