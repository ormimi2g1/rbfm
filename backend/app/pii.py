"""Strip personal details before any text leaves the app (AI fallback) or is
stored. Company names, invoice and shipment numbers are kept."""
import re

_DROP_LINE = re.compile(
    r"(please call|questions regarding|attn\s*:|contact\s*:|prepared by|"
    r"bank|routing|swift|iban|aba\b|account\s*(no|number|#)|wire|ach\s+(to|details)|remit)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")  # formatted numbers only: bare digit runs are invoice numbers


def redact_lines(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if _DROP_LINE.search(ln) or _EMAIL.search(ln):
            continue
        out.append(ln)
    return out


def redact_text(text: str) -> str:
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text))
