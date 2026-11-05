"""String IDs with the original bilingual catalog labels."""

from enum import StrEnum
from typing import Self


class CatalogId(StrEnum):
    """An ID that serializes like a string and retains its human-readable labels."""

    label_en: str
    label_he: str

    def __new__(cls, value: str, label_en: str = "", label_he: str = "") -> Self:
        member = str.__new__(cls, value)
        member._value_ = value
        member.label_en = label_en
        member.label_he = label_he
        return member
