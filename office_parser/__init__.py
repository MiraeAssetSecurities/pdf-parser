from .parser import OfficeParser
from .types import (
    OfficeParserConfig,
    OfficeParserAST,
    OfficeContentNode,
    OfficeMetadata,
    OfficeAttachment,
    TextFormatting,
    ChartData,
)

__version__ = "1.0.0"
__all__ = [
    "OfficeParser",
    "OfficeParserConfig",
    "OfficeParserAST",
    "OfficeContentNode",
    "OfficeMetadata",
    "OfficeAttachment",
    "TextFormatting",
    "ChartData",
]

parse_office = OfficeParser.parse_office
