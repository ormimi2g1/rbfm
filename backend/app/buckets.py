"""Charge buckets: which bill lines are government customs, broker duty fees,
or freight & handling. Rules live in the database so they can be edited."""
import re

from sqlalchemy.orm import Session

from .models import BucketRule, Setting

DEFAULT_RULES = [
    (r"duty subject to liquidation", "customs"),
    (r"merchandise processing fee", "customs"),
    (r"harbor maintenance fee", "customs"),
    (r"cotton fee", "customs"),
    (r"^duty$|^duties$|customs duty", "customs"),
    (r"customs disbursement fee", "broker_fee"),
    (r"duty management fee", "broker_fee"),
]
DEFAULT_SETTINGS = {"variance_abs": "1.00", "variance_pct": "2.0"}
MAIN_FREIGHT_RE = re.compile(r"\b(air|ocean|sea)\s*freight\b", re.I)


def seed_defaults(db: Session):
    if not db.query(BucketRule).first():
        for i, (pat, bucket) in enumerate(DEFAULT_RULES):
            db.add(BucketRule(pattern=pat, bucket=bucket, position=i))
    for k, v in DEFAULT_SETTINGS.items():
        if not db.get(Setting, k):
            db.add(Setting(key=k, value=v))
    db.commit()


def load_rules(db: Session):
    return [(re.compile(r.pattern, re.I), r.bucket) for r in db.query(BucketRule).order_by(BucketRule.position, BucketRule.id)]


def classify(description: str, rules) -> str:
    for rx, bucket in rules:
        if rx.search(description or ""):
            return bucket
    return "freight"   # everything else is freight & handling


def settings(db: Session) -> dict:
    return {s.key: s.value for s in db.query(Setting)}
