#!/usr/bin/env python3
"""Fetch UK public tender notices and email relevant opportunities.

This is an MVP worker. Review its output before using it for paid customer
delivery, because source schemas and procurement terminology can change.
"""
from __future__ import annotations

import json
import os
import smtplib
import sqlite3
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "subscribers.json"
DB_PATH = ROOT / "data" / "tender_signal.sqlite3"
API_URL = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"


def load_subscribers() -> list[dict]:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Missing {CONFIG_PATH}. Copy subscribers.example.json first.")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def fetch_notices() -> list[dict]:
    # A 24-hour overlap protects against a delayed or failed scheduled run.
    # SQLite delivery de-duplication prevents an already-sent notice from being
    # repeated when the overlap includes it again.
    lookback_hours = int(os.getenv("TENDERSIGNAL_LOOKBACK_HOURS", "24"))
    updated_from = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).replace(microsecond=0)
    params = {"limit": "100", "stages": "tender", "updatedFrom": updated_from.isoformat()}
    request = Request(f"{API_URL}?{urlencode(params)}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        package = json.load(response)
    return package.get("releases", [])


def text_for(release: dict) -> str:
    tender = release.get("tender", {})
    parts = [
        release.get("title", ""),
        tender.get("title", ""),
        tender.get("description", ""),
        " ".join(tender.get("classification", {}).values()) if isinstance(tender.get("classification"), dict) else "",
    ]
    return " ".join(str(part) for part in parts).lower()


def score(release: dict, subscriber: dict) -> tuple[int, list[str]]:
    haystack = text_for(release)
    score_value = 0
    reasons: list[str] = []
    for term in subscriber.get("include_terms", []):
        if term.lower() in haystack:
            score_value += 3
            reasons.append(term)
    for term in subscriber.get("exclude_terms", []):
        if term.lower() in haystack:
            score_value -= 8
    delivery = release.get("tender", {}).get("deliveryAddresses", [])
    regions = " ".join(json.dumps(address) for address in delivery).lower()
    for region in subscriber.get("regions", []):
        if region.lower() in regions:
            score_value += 1
            reasons.append(region)
            break
    return score_value, reasons


def source_link(release: dict) -> str:
    notice_id = release.get("id", "")
    return f"https://www.find-tender.service.gov.uk/Notice/{notice_id}" if notice_id else "https://www.find-tender.service.gov.uk/"


def already_sent(connection: sqlite3.Connection, subscriber_id: str, notice_id: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM deliveries WHERE subscriber_id = ? AND notice_id = ?", (subscriber_id, notice_id)
    ).fetchone()
    return row is not None


def mark_sent(connection: sqlite3.Connection, subscriber_id: str, notice_id: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO deliveries(subscriber_id, notice_id, sent_at) VALUES (?, ?, ?)",
        (subscriber_id, notice_id, datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()


def send_email(recipient: str, subject: str, html: str) -> None:
    required = ["TENDERSIGNAL_SMTP_HOST", "TENDERSIGNAL_SMTP_USER", "TENDERSIGNAL_SMTP_PASSWORD", "TENDERSIGNAL_SMTP_FROM"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing email configuration: {', '.join(missing)}")
    message = EmailMessage()
    message["From"] = os.environ["TENDERSIGNAL_SMTP_FROM"]
    message["To"] = recipient
    message["Subject"] = subject
    unsubscribe_address = os.getenv("TENDERSIGNAL_UNSUBSCRIBE_ADDRESS") or os.environ["TENDERSIGNAL_SMTP_FROM"]
    message["List-Unsubscribe"] = f"<mailto:{unsubscribe_address}?subject=UNSUBSCRIBE>"
    message.set_content("View this TenderSignal alert in an HTML-capable email client.")
    message.add_alternative(html, subtype="html")
    port = int(os.getenv("TENDERSIGNAL_SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(os.environ["TENDERSIGNAL_SMTP_HOST"], port, context=ssl.create_default_context()) as server:
        server.login(os.environ["TENDERSIGNAL_SMTP_USER"], os.environ["TENDERSIGNAL_SMTP_PASSWORD"])
        server.send_message(message)


def main() -> int:
    if os.getenv("TENDERSIGNAL_SMOKE_TEST", "").lower() == "true":
        recipient = os.environ["TENDERSIGNAL_SMTP_FROM"]
        send_email(
            recipient,
            "TenderSignal delivery test",
            "<h2>TenderSignal is connected</h2><p>This is a manual delivery test. No customer notice was sent.</p>",
        )
        print(f"Sent delivery test to {recipient}")
        return 0
    subscribers = load_subscribers()
    notices = fetch_notices()
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS deliveries (subscriber_id TEXT, notice_id TEXT, sent_at TEXT, UNIQUE(subscriber_id, notice_id))")
        for subscriber in subscribers:
            if subscriber.get("status", "active") != "active":
                continue
            for notice in notices:
                notice_id = notice.get("id") or notice.get("ocid")
                if not notice_id or already_sent(connection, subscriber["id"], notice_id):
                    continue
                relevance, reasons = score(notice, subscriber)
                if relevance < subscriber.get("minimum_score", 7):
                    continue
                title = notice.get("tender", {}).get("title") or notice.get("title") or "Untitled procurement notice"
                reason_text = ", ".join(reasons) or "profile match"
                html = f"""<h2>TenderSignal: {escape(title)}</h2>
<p><strong>Match score:</strong> {relevance}/10 &middot; {escape(reason_text)}</p>
<p>{escape(notice.get('tender', {}).get('description', '')[:1200])}</p>
<p><a href=\"{escape(source_link(notice), quote=True)}\">Open the official notice</a></p>
<p><small>You received this because your company profile is active. To stop alerts, reply with the word UNSUBSCRIBE.</small></p>"""
                send_email(subscriber["email"], f"TenderSignal match ({relevance}/10): {title}", html)
                mark_sent(connection, subscriber["id"], notice_id)
                print(f"Sent {notice_id} to {subscriber['email']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
