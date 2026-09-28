from pathlib import Path

from app.parser.txt_parser import (
    parse_txt,
    parse_inspection_txt,
)


BASE_DIR = Path(__file__).resolve().parents[1]

MOCK_DIR = (
    BASE_DIR /
    "tests" /
    "mock_data"
)


def main():

    print("=" * 60)
    print("IV4 TXT Parser Test")
    print("=" * 60)

    # --------------------------------------------------
    # Test individual TXT
    # --------------------------------------------------

    txt_file = MOCK_DIR / "006.txt"

    print()
    print(f"[PARSING] {txt_file.name}")

    data = parse_txt(txt_file)

    for key, value in data.items():

        print(
            f"  {key:<20} = {value!r}"
        )

    # --------------------------------------------------
    # Test inspection
    # --------------------------------------------------

    print()
    print("[PARSING] Inspection")

    result = parse_inspection_txt(
        MOCK_DIR
    )

    print(
        f"  Inspection ID: "
        f"{result['inspection_id']}"
    )

    print(
        f"  TXT Files: "
        f"{len(result['files'])}"
    )

    for filename, data in result["files"].items():

        print()
        print(
            f"  [{filename}]"
        )

        for key, value in data.items():

            print(
                f"    {key:<18} = {value!r}"
            )

    print()
    print("=" * 60)
    print("PARSER TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()