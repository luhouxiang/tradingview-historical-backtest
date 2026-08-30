#!/usr/bin/env python3
"""Verify candidate passwords against encrypted ZIP files under ebooks.

The tool never extracts archive members.  It supports traditional ZipCrypto,
which is handled by Python's standard library.  WinZip AES archives are
reported as unsupported instead of being modified or partially extracted.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import shutil
import struct
import sys
import time
import zipfile
import zlib
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile

READ_CHUNK_SIZE = 1024 * 1024
LOCAL_FILE_HEADER = struct.Struct("<4s2B4HL2L2H")
LOCAL_FILE_SIGNATURE = b"PK\x03\x04"
ENCRYPTION_HEADER_SIZE = 12


@dataclass(frozen=True)
class Result:
    archive: str
    status: str
    password: str | None
    password_encoding: str | None
    attempts: int
    elapsed_seconds: float
    detail: str


def parse_digit_range(value: str) -> tuple[int, int, int]:
    parts = value.split(":", maxsplit=1)
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise argparse.ArgumentTypeError("数字范围必须是 START:END，例如 000000:999999")
    start_text, end_text = parts
    start, end = int(start_text), int(end_text)
    if end < start:
        raise argparse.ArgumentTypeError("数字范围的 END 不能小于 START")
    return start, end, max(len(start_text), len(end_text))


def parse_encodings(value: str) -> tuple[str, ...]:
    names: list[str] = []
    for raw_name in value.split(","):
        name = raw_name.strip()
        if not name:
            continue
        try:
            canonical = codecs.lookup(name).name
        except LookupError as exc:
            raise argparse.ArgumentTypeError(f"未知密码编码：{name}") from exc
        if canonical not in names:
            names.append(canonical)
    if not names:
        raise argparse.ArgumentTypeError("至少需要一种密码编码")
    return tuple(names)


def candidate_texts(args: argparse.Namespace) -> Iterator[str]:
    yield from args.password
    for wordlist in args.wordlist:
        try:
            with wordlist.open("r", encoding="utf-8-sig", newline="") as handle:
                for line in handle:
                    candidate = line.rstrip("\r\n")
                    if candidate:
                        yield candidate
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(f"无法读取候选密码文件 {wordlist}: {exc}") from exc
    for start, end, width in args.digits:
        for number in range(start, end + 1):
            yield str(number).zfill(width)


def candidate_bytes(
    args: argparse.Namespace,
) -> Iterator[tuple[str, str, bytes]]:
    seen: set[bytes] = set()
    for text in candidate_texts(args):
        for encoding in args.password_encodings:
            try:
                encoded = text.encode(encoding)
            except UnicodeEncodeError:
                continue
            if not encoded or encoded in seen:
                continue
            seen.add(encoded)
            yield text, encoding, encoded


def is_aes_member(info: zipfile.ZipInfo) -> bool:
    # Compression method 99 and extra-field header 0x9901 identify WinZip AES.
    return info.compress_type == 99 or b"\x01\x99" in info.extra


def verify_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, password: bytes
) -> bool:
    try:
        with archive.open(info, mode="r", pwd=password) as member:
            while member.read(READ_CHUNK_SIZE):
                pass
    except (RuntimeError, zipfile.BadZipFile, NotImplementedError, zlib.error):
        return False
    return True


def read_encryption_header(path: Path, info: zipfile.ZipInfo) -> tuple[bytes, int]:
    with path.open("rb") as handle:
        handle.seek(info.header_offset)
        raw_header = handle.read(LOCAL_FILE_HEADER.size)
        if len(raw_header) != LOCAL_FILE_HEADER.size:
            raise zipfile.BadZipFile("截断的 ZIP 本地文件头")
        fields = LOCAL_FILE_HEADER.unpack(raw_header)
        if fields[0] != LOCAL_FILE_SIGNATURE:
            raise zipfile.BadZipFile("无效的 ZIP 本地文件头签名")
        filename_length, extra_length = fields[10], fields[11]
        handle.seek(filename_length + extra_length, 1)
        encrypted_header = handle.read(ENCRYPTION_HEADER_SIZE)
        if len(encrypted_header) != ENCRYPTION_HEADER_SIZE:
            raise zipfile.BadZipFile("截断的 ZipCrypto 加密头")

    check_byte = (
        (info._raw_time >> 8) & 0xFF
        if info.flag_bits & 0x8
        else (info.CRC >> 24) & 0xFF
    )
    return encrypted_header, check_byte


def encryption_header_matches(
    encrypted_header: bytes, check_byte: int, password: bytes
) -> bool:
    # ZipCrypto exposes a one-byte password check.  A match is only a fast
    # prefilter; verify_member still consumes the complete member and its CRC.
    decrypter = zipfile._ZipDecrypter(password)
    return decrypter(encrypted_header)[-1] == check_byte


def find_password(path: Path, args: argparse.Namespace) -> Result:
    started = time.monotonic()
    attempts = 0
    try:
        with zipfile.ZipFile(path) as archive:
            encrypted = [
                info
                for info in archive.infolist()
                if not info.is_dir() and info.flag_bits & 0x1
            ]
            if not encrypted:
                return Result(
                    str(path),
                    "not_encrypted",
                    None,
                    None,
                    0,
                    time.monotonic() - started,
                    "ZIP 中没有加密文件",
                )
            if any(is_aes_member(info) for info in encrypted):
                return Result(
                    str(path),
                    "unsupported_aes",
                    None,
                    None,
                    0,
                    time.monotonic() - started,
                    "检测到 WinZip AES；此脚本只使用标准库验证 ZipCrypto",
                )

            probe = min(encrypted, key=lambda info: (info.compress_size, info.filename))
            encrypted_header, check_byte = read_encryption_header(path, probe)
            for text, encoding, password in candidate_bytes(args):
                if attempts >= args.max_attempts:
                    break
                attempts += 1
                if args.progress_every and attempts % args.progress_every == 0:
                    elapsed = time.monotonic() - started
                    print(
                        f"[{path.name}] 已尝试 {attempts:,} 次，耗时 {elapsed:.1f}s",
                        file=sys.stderr,
                        flush=True,
                    )
                if not encryption_header_matches(
                    encrypted_header, check_byte, password
                ):
                    continue
                if not verify_member(archive, probe, password):
                    continue

                # A ZIP may technically use a different password per member.  Only
                # call this an archive match when the candidate verifies all of them.
                if all(
                    info is probe or verify_member(archive, info, password)
                    for info in encrypted
                ):
                    return Result(
                        str(path),
                        "found",
                        text,
                        encoding,
                        attempts,
                        time.monotonic() - started,
                        f"已完整校验 {len(encrypted)} 个加密文件的 CRC",
                    )

            detail = "候选密码未命中"
            if attempts >= args.max_attempts:
                detail = f"达到尝试上限 {args.max_attempts:,}，已停止"
            return Result(
                str(path),
                "not_found",
                None,
                None,
                attempts,
                time.monotonic() - started,
                detail,
            )
    except (OSError, zipfile.BadZipFile) as exc:
        return Result(
            str(path),
            "invalid_archive",
            None,
            None,
            attempts,
            time.monotonic() - started,
            str(exc),
        )


def extract_verified_archive(
    path: Path, password: str, encoding: str, output: Path
) -> None:
    destination = output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    password_bytes = password.encode(encoding)
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            member_path = PurePosixPath(info.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError(f"拒绝不安全的 ZIP 成员路径：{info.filename}")
            target = destination.joinpath(*member_path.parts).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise RuntimeError(f"ZIP 成员越过输出目录：{info.filename}") from exc
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists():
                raise RuntimeError(f"目标文件已存在，拒绝覆盖：{target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_name: str | None = None
            try:
                with (
                    archive.open(info, mode="r", pwd=password_bytes) as source,
                    NamedTemporaryFile(
                        mode="wb",
                        dir=target.parent,
                        prefix=f".{target.name}.",
                        delete=False,
                    ) as temporary,
                ):
                    temporary_name = temporary.name
                    shutil.copyfileobj(source, temporary, READ_CHUNK_SIZE)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, target)
                temporary_name = None
            finally:
                if temporary_name is not None:
                    Path(temporary_name).unlink(missing_ok=True)


def discover_archives(directory: Path, recursive: bool) -> list[Path]:
    root = directory.resolve(strict=True)
    pattern = "**/*.zip" if recursive else "*.zip"
    archives: list[Path] = []
    for candidate in directory.glob(pattern):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        archives.append(resolved)
    return sorted(archives, key=lambda path: str(path).casefold())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用候选密码验证 ebooks 目录中的加密 ZIP（不解压、不修改原文件）"
    )
    parser.add_argument(
        "--directory", type=Path, default=Path("ebooks"), help="ZIP 目录（默认 ebooks）"
    )
    parser.add_argument(
        "--recursive", action="store_true", help="递归扫描子目录中的 ZIP"
    )
    parser.add_argument(
        "--wordlist",
        type=Path,
        action="append",
        default=[],
        help="UTF-8 候选密码文件，每行一个；可重复指定",
    )
    parser.add_argument(
        "--password",
        action="append",
        default=[],
        help="直接提供一个候选密码；可重复指定（可能出现在命令历史中）",
    )
    parser.add_argument(
        "--digits",
        type=parse_digit_range,
        action="append",
        default=[],
        metavar="START:END",
        help="尝试含前导零的纯数字闭区间，例如 0000:9999；可重复指定",
    )
    parser.add_argument(
        "--password-encodings",
        type=parse_encodings,
        default=parse_encodings("utf-8,gb18030"),
        metavar="LIST",
        help="逗号分隔的密码字节编码（默认 utf-8,gb18030）",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=100_000,
        help="每个 ZIP 的最大字节密码尝试次数（默认 100000）",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1_000,
        help="每多少次向 stderr 报告进度；0 表示关闭（默认 1000）",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="写入 JSON 结果；注意命中时文件会包含明文密码",
    )
    parser.add_argument(
        "--extract-to",
        type=Path,
        help="命中并完整校验后解压到此目录；拒绝路径逃逸和覆盖已有文件",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not (args.password or args.wordlist or args.digits):
        parser.error("至少指定 --wordlist、--password 或 --digits 之一")
    if args.max_attempts <= 0:
        parser.error("--max-attempts 必须大于 0")
    if args.progress_every < 0:
        parser.error("--progress-every 不能小于 0")
    try:
        archives = discover_archives(args.directory, args.recursive)
    except OSError as exc:
        parser.error(f"无法读取 ZIP 目录：{exc}")
    if not archives:
        parser.error(f"目录中没有找到 ZIP：{args.directory}")

    results: list[Result] = []
    try:
        for archive in archives:
            result = find_password(archive, args)
            results.append(result)
            if result.status == "found":
                if args.extract_to:
                    assert result.password is not None
                    assert result.password_encoding is not None
                    extract_verified_archive(
                        archive,
                        result.password,
                        result.password_encoding,
                        args.extract_to,
                    )
                print(
                    f"找到：{archive.name}\n"
                    f"  密码：{result.password}\n"
                    f"  编码：{result.password_encoding}\n"
                    f"  尝试：{result.attempts:,}\n"
                    f"  校验：{result.detail}"
                )
            else:
                print(f"未找到：{archive.name} [{result.status}] {result.detail}")
    except RuntimeError as exc:
        parser.error(str(exc))

    if args.json_output:
        output = args.json_output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                [asdict(result) for result in results], ensure_ascii=False, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"结果已写入：{output}")

    return (
        0
        if all(result.status in {"found", "not_encrypted"} for result in results)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
