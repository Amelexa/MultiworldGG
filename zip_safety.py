"""Shared limits for inspecting untrusted ZIP archives before reading members."""

import zipfile


MAX_ARCHIVE_MEMBERS = 4096
MAX_MEMBER_SIZE = 64 * 1024 * 1024
MAX_TOTAL_SIZE = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


class UnsafeZipError(ValueError):
    pass


def validate_zip(
    archive: zipfile.ZipFile,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_member_size: int = MAX_MEMBER_SIZE,
    max_total_size: int = MAX_TOTAL_SIZE,
    max_ratio: int = MAX_COMPRESSION_RATIO,
) -> None:
    """Reject archives whose metadata indicates a decompression bomb."""
    members = archive.infolist()
    if len(members) > max_members:
        raise UnsafeZipError(f"ZIP contains too many entries (maximum {max_members})")

    total_size = 0
    for member in members:
        if member.flag_bits & 0x1:
            raise UnsafeZipError("Encrypted ZIP entries are not supported")
        if member.file_size > max_member_size:
            raise UnsafeZipError(
                f"ZIP entry '{member.filename}' is too large when unpacked "
                f"(maximum {max_member_size // (1024 * 1024)} MiB)"
            )
        total_size += member.file_size
        if total_size > max_total_size:
            raise UnsafeZipError(
                f"ZIP expands beyond the {max_total_size // (1024 * 1024)} MiB limit"
            )
        if member.file_size > 1024 * 1024:
            ratio = member.file_size / max(member.compress_size, 1)
            if ratio > max_ratio:
                raise UnsafeZipError(
                    f"ZIP entry '{member.filename}' has a suspicious compression ratio"
                )
