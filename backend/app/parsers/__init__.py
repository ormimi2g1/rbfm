"""parse_pdf(): rules for known forwarders first, AI reader as fallback.
A result only counts as good when it ties to the bill's printed total."""
from pathlib import Path

from .. import config
from . import ai, chr as chr_, crane, expeditors, pdftext
from .base import ParsedBill

RULE_PARSERS = [chr_, crane, expeditors]


def parse_pages(pages: list[list[str]], used_ocr: bool = False, ai_client=None) -> ParsedBill | None:
    text = "\n".join(l for p in pages for l in p)
    result = None
    for mod in RULE_PARSERS:
        if mod.matches(text):
            result = mod.parse(pages)
            break
    if (result is None or not result.tied(config.TIE_TOLERANCE)) and text.strip():
        via_ai = ai.parse(pages, client=ai_client)
        if via_ai is not None and (result is None or via_ai.tied(config.TIE_TOLERANCE)):
            result = via_ai
    if result is not None and used_ocr:
        result.method += "+ocr"
        result.confidence = round(result.confidence * 0.7, 2)
        result.warnings.append("Read from a scanned image (OCR): lower confidence")
    return result


def parse_pdf(path: str | Path) -> ParsedBill | None:
    pages, used_ocr = pdftext.extract_pages(path, config.PARSE_PAGES)
    return parse_pages(pages, used_ocr)
