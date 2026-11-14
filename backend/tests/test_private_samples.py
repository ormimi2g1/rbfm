"""Runs against real bills kept OUTSIDE the repository.

Set RBFM_PRIVATE_DIR to a folder holding the PDFs and an expected.json like
{"<file name>.pdf": {"invoice_no": "...", "subtotal": 0.0, "lines": 0}}."""
import json
import os
from pathlib import Path

import pytest

from app.parsers import parse_pdf

DIR = os.environ.get("RBFM_PRIVATE_DIR")
pytestmark = pytest.mark.skipif(not DIR or not Path(DIR, "expected.json").exists(), reason="no private samples")


def test_real_bills_tie():
    expected = json.loads(Path(DIR, "expected.json").read_text())
    for name, want in expected.items():
        b = parse_pdf(Path(DIR, name))
        assert b is not None and b.tied(), name
        assert b.invoice_no == want["invoice_no"] and b.subtotal == want["subtotal"] and len(b.charges) == want["lines"], name
