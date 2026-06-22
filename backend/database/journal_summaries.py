"""
Journal summaries (web UI "Jornal do dia") — persisted per-day AI summary.

users/{uid}/journal_summaries/{date}   where date = YYYY-MM-DD

Distinct from the Omi nightly `daily_summaries` (different schema/purpose):
this stores the on-demand journal summary generated in the web UI so it is not
regenerated every time the day is opened.
"""

from datetime import datetime, timezone
from typing import Optional

from database._client import db

JOURNAL_SUMMARIES_COLLECTION = 'journal_summaries'


def get_journal_summary(uid: str, date: str) -> Optional[dict]:
    """Get the stored journal summary for a date (YYYY-MM-DD), or None."""
    doc = (
        db.collection('users')
        .document(uid)
        .collection(JOURNAL_SUMMARIES_COLLECTION)
        .document(date)
        .get()
    )
    return doc.to_dict() if doc.exists else None


def set_journal_summary(uid: str, date: str, summary: str) -> dict:
    """Create/overwrite the journal summary for a date. Returns the stored doc."""
    data = {
        'date': date,
        'summary': summary,
        'updated_at': datetime.now(timezone.utc),
    }
    db.collection('users').document(uid).collection(JOURNAL_SUMMARIES_COLLECTION).document(date).set(data)
    return data


def delete_journal_summary(uid: str, date: str) -> None:
    """Delete the stored journal summary for a date."""
    db.collection('users').document(uid).collection(JOURNAL_SUMMARIES_COLLECTION).document(date).delete()
