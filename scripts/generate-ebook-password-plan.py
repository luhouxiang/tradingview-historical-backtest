#!/usr/bin/env python3
"""Build finite, auditable password-recovery inputs for the ebook ZIP."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import string
from pathlib import Path
from tempfile import NamedTemporaryFile

DIGITS = string.digits
LOWERCASE = string.ascii_lowercase
UPPERCASE = string.ascii_uppercase
SPECIALS = string.punctuation
COMMON_SPECIALS = "!@#$%&*_-?."


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def digit_tokens() -> list[str]:
    # Exhaust every one-to-four digit block, including leading-zero forms.
    values = [str(number).zfill(width) for width in range(1, 5) for number in range(10**width)]
    values.extend(
        [
            "9787111642602",
            "7111642602",
            "11642602",
            "1642602",
            "20210420",
            "20200420",
            "201912",
            "202001",
            "0420",
            "2019",
            "2020",
            "2021",
        ]
    )
    return unique(values)


def date_tokens() -> list[str]:
    values: list[str] = []
    current = dt.date(1950, 1, 1)
    end = dt.date(2021, 12, 31)
    while current <= end:
        values.extend((current.strftime("%Y%m%d"), current.strftime("%y%m%d")))
        current += dt.timedelta(days=1)
    values.extend(
        f"{year:04d}{month:02d}"
        for year in range(1950, 2022)
        for month in range(1, 13)
    )
    return unique(values)


def contextual_candidates() -> list[str]:
    words = [
        "ml",
        "ML",
        "ai",
        "AI",
        "py",
        "PY",
        "python",
        "Python",
        "PYTHON",
        "mofan",
        "MoFan",
        "book",
        "ebook",
    ]
    numbers = ["0420", "2020", "2021", "20210420", "9787111642602"]
    specials = list(COMMON_SPECIALS)
    candidates = [
        "nzjv",
        "pdfs.top",
        "www.pdfs.top",
        "www.j9p.com",
        "www.d4j.cn",
        "www.pdfzj.com",
        "www.ireadweek.com",
        "www.ebook80.com",
        "www.book123.info",
        "www.allitebooks.com",
        "it-ebooks.info",
        "www.chendianrong.com",
        "chendianrong.com",
        "www.xuejis.com",
        "www.480032.com",
        "www.4aqq.com",
        "baoshuk.com",
        "www.kgbook.com",
        "kgbook.com",
        "www.sobooks.cc",
        "sobooks.cc",
        "www.haodoo.net",
        "www.52ebook.com",
        "52ebook.com",
        "www.zxcs.me",
        "www.zxcs.info",
        "www.shukeba.com",
    ]
    for word in words:
        candidates.append(word)
        for number in numbers:
            candidates.extend((word + number, number + word))
            for special in specials:
                candidates.extend(
                    (
                        word + number + special,
                        word + special + number,
                        number + word + special,
                        number + special + word,
                        special + word + number,
                        special + number + word,
                    )
                )
    return unique(candidate for candidate in candidates if len(candidate) <= 16)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("ebooks/password-plan"))
    args = parser.parse_args()
    output = args.output.resolve()

    table = {
        "digits": DIGITS,
        "lowercase_letters": LOWERCASE,
        "uppercase_letters": UPPERCASE,
        "special_characters": SPECIALS,
        "all_printable_non_space_ascii": DIGITS + LOWERCASE + UPPERCASE + SPECIALS,
        "counts": {"digits": 10, "lowercase": 26, "uppercase": 26, "specials": 32, "all": 94},
        "maximum_password_length": 16,
        "habit_constraints": {"alphabetic_block_lengths": [2, 3], "special_block_lengths": [1, 2]},
    }
    tokens = digit_tokens()
    dates = date_tokens()
    candidates = contextual_candidates()
    phases = {
        "orders": ["ADS", "ASD", "DAS", "DSA", "SAD", "SDA"],
        "alpha_charsets": [LOWERCASE, UPPERCASE, LOWERCASE + UPPERCASE],
        "alpha_lengths": [2, 3],
        "special_charsets": [COMMON_SPECIALS, SPECIALS],
        "special_lengths": [1, 2],
        "digit_token_count": len(tokens),
        "date_token_count": len(dates),
        "context_candidate_count": len(candidates),
        "note": "Each phase is a Cartesian product of A/D/S blocks; duplicate broader phases are retained for simple resumability.",
    }
    atomic_text(output / "character-table.json", json.dumps(table, ensure_ascii=False, indent=2) + "\n")
    atomic_text(output / "digit-tokens-priority.txt", "\n".join(tokens) + "\n")
    atomic_text(output / "digit-tokens-dates.txt", "\n".join(dates) + "\n")
    atomic_text(
        output / "digit-tokens-length-5.txt",
        "\n".join(f"{number:05d}" for number in range(100_000)) + "\n",
    )
    atomic_text(
        output / "digit-tokens-length-6.txt",
        "\n".join(f"{number:06d}" for number in range(1_000_000)) + "\n",
    )
    atomic_text(output / "context-candidates.txt", "\n".join(candidates) + "\n")
    atomic_text(output / "plan.json", json.dumps(phases, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(output),
                "digit_tokens": len(tokens),
                "date_tokens": len(dates),
                "context_candidates": len(candidates),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
