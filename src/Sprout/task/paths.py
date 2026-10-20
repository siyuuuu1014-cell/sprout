"""Pure component-based path authority. No filesystem, CWD or DNS lookups."""

from __future__ import annotations

import ntpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath


@dataclass(frozen=True)
class ScopePath:
    flavour: str
    anchor: str
    parts: tuple[str, ...]

    def contains(self, other: ScopePath) -> bool:
        return (self.flavour == other.flavour and self.anchor == other.anchor
                and other.parts[:len(self.parts)] == self.parts)

    def text(self) -> str:
        return self.anchor + "/".join(self.parts) if self.anchor else "/".join(self.parts) or "."


def scope_path(value: str) -> ScopePath | None:
    """POSIX is case-sensitive; Windows uses its lexical normcase convention.

    UNC is a lexical family only: parsing never contacts the share. Device/ADS,
    DOS aliases, drive-relative and root-relative Windows forms are unprovable.
    Relative paths compare only to the same relative family until a broker binds
    them to its trusted absolute root. A leading '..' is never a trusted base.
    """
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        return None
    normalized = value.replace("\\", "/")
    if normalized.startswith(("//?/", "//./", "/??/")):
        return None
    windows = bool(re.match(r"^[A-Za-z]:", value) or "\\" in value or value.startswith("//"))
    pure = PureWindowsPath(value) if windows else PurePosixPath(value)
    if windows:
        if (pure.drive and not pure.root) or (pure.root and not pure.drive):
            return None
        anchor = pure.anchor.replace("\\", "/").lower()
        if pure.drive.startswith("\\\\"):
            server_share = pure.drive[2:].split("\\")
            if (len(server_share) != 2 or not all(server_share)
                    or any(p.endswith((".", " ")) or re.search(r'[<>:"|?*]', p)
                           for p in server_share)):
                return None
        parts = pure.parts[1:] if pure.anchor else pure.parts
    else:
        anchor = pure.anchor
        parts = pure.parts[1:] if pure.anchor else pure.parts
    stack: list[str] = []
    for part in parts:
        if part == ".":
            continue
        if part == "..":
            if not stack:
                return None
            stack.pop()
            continue
        if windows:
            if (part.endswith((".", " ")) or re.search(r'[<>:"|?*\x00-\x1f]', part)
                    or re.fullmatch(r"(?:con|prn|aux|nul|clock\$|com[0-9¹²³]|lpt[0-9¹²³])",
                                    part.split(".")[0], re.IGNORECASE)):
                return None
            part = ntpath.normcase(part)
        stack.append(part)
    return ScopePath("windows" if windows else "posix", anchor, tuple(stack))


def bound_scope_path(value: str, root: str) -> str | None:
    """Bind a relative grant only to a server-supplied absolute root."""
    base, path = scope_path(root), scope_path(value)
    if base is None or not base.anchor or path is None:
        return None
    if path.anchor:
        return path.text()
    # A plain relative 'src/a' is portable; an explicit backslash-relative path
    # is Windows-only, never interpreted as a POSIX filename or a CWD grant.
    if path.flavour == "windows" and base.flavour != "windows":
        return None
    return ScopePath(base.flavour, base.anchor, (*base.parts, *path.parts)).text()
