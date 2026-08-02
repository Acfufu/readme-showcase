"""Fixture that must never be imported by an extractor."""


def main() -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
