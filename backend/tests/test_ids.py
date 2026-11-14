from app import ids


def test_normalise_and_parse():
    assert ids.normalise("2508-xyz-AIR- 3") == "2508-XYZ-AIR-3"
    assert ids.parse("2508-ABC-BOAT-15")["mode"] == "BOAT"
    assert ids.parse("2407-ABC-BOAT-13.5")["seq"] == "13.5"
    assert ids.parse("2509-DEF-AIR-9-B")["suffix"] == "B"
    assert ids.parse("250-LMN-AIR-1") is None and ids.parse("GHI-AIR") is None


def test_format_issues():
    assert ids.format_issues("2509-ABC-AIR-4") == []
    assert "contains spaces" in ids.format_issues("2508-XYZ-AIR- 3")
    assert "mixed case" in ids.format_issues("2601-QrSt-Boat-1")
    assert any("YYMM" in i for i in ids.format_issues("2510-GHI-8.1"))
    assert "month is not 01-12" in ids.format_issues("2513-ABC-AIR-1")


def test_po_normalisation():
    assert ids.normalise_pos("009043,9033") == ["PO009043", "PO009033"]
    assert ids.normalise_pos("PO 9195 /PO 9234") == ["PO009195", "PO009234"]
    assert ids.normalise_pos("PO009044,PO009056 , PO009044") == ["PO009044", "PO009056"]


def test_resolve():
    known = {"2509-LMN-AIR-1": {"PO009300"}, "2509-ABC-AIR-4": {"PO009043"}}
    assert ids.resolve("2509-ABC-AIR-4", known, {}).method == "exact"
    assert ids.resolve("2509-abc-air- 4", known, {}).method == "normalised"
    r = ids.resolve("250-LMN-AIR-1", known, {})                       # typo: suggested, not applied
    assert r.shipment_id is None and r.suggestions[0]["shipment_id"] == "2509-LMN-AIR-1"
    assert ids.resolve("250-LMN-AIR-1", known, {"250-LMN-AIR-1": "2509-LMN-AIR-1"}).method == "alias"
    r = ids.resolve(None, known, {}, ["PO009043"])                    # no ID on the file: PO evidence
    assert r.suggestions[0]["shipment_id"] == "2509-ABC-AIR-4" and "PO009043" in r.suggestions[0]["reason"]


def test_from_filename():
    assert ids.from_filename("2602-ABC-AIR-4_INV6200000001.pdf") == ("2602-ABC-AIR-4", "6200000001")
    assert ids.from_filename("2508-XYZ-AIR- 3_6200000003 (1).pdf") == ("2508-XYZ-AIR- 3", "6200000003")
    assert ids.from_filename("Bill_6200000003_x.pdf") == (None, None)
