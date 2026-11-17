"""2.4.0 step 2 (#102): write message_help.json (the Findings panel's "what to do" words) from the user's manual,
chapter 23 (Messages explained). Run it after editing chapter 23:

    python tools/build_message_help.py

docs/ is not in the release zip, so the app reads this JSON, not the manual. tests/test_240_d.py fails when the JSON
and chapter 23 disagree, so the panel and the manual always say the same thing."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import findings  # noqa: E402

CH23 = os.path.join(ROOT, "docs", "manual", "23-messages-explained.md")


def build() -> dict:
    with open(CH23, encoding="utf-8") as fh:
        rows = findings.parse_chapter23(fh.read())
    return {"source": "docs/manual/23-messages-explained.md (tools/build_message_help.py)", "rows": rows}


def main():
    data = build()
    with open(findings.HELP_FILE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{len(data['rows'])} messages → {findings.HELP_FILE}")


if __name__ == "__main__":
    main()
