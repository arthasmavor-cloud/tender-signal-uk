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
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "subscribers.json"
DB_PATH = ROOT / "data" / "tender_signal.sqlite3"
API_URL = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"


def load_subscribers() -> list[dict]:
    endpoint = os.getenv("TENDERSIGNAL_SUBSCRIBERS_ENDPOINT", "").strip()
    if endpoint:
        token = os.getenv("TENDERSIGNAL_SUBSCRIBERS_TOKEN", "").strip()
        if not token:
            raise SystemExit("Missing TENDERSIGNAL_SUBSCRIBERS_TOKEN for the subscriber endpoint.")
        separator = "&" if "?" in endpoint else "?"
        source = f"{endpoint}{separator}mode=subscribers&token={quote(token, safe='')}"
        try:
            payload = fetch_package(source)
        except RuntimeError as error:
            raise SystemExit(f"Could not load active subscribers: {error}") from error
        if not isinstance(payload, list):
            raise SystemExit("Subscriber endpoint did not return a list of active profiles.")
        return payload
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Missing {CONFIG_PATH}. Copy subscribers.example.json first.")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def fetch_package(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < 3:
                wait_seconds = attempt * 3
                print(f"Tender source attempt {attempt}/3 failed: {error}. Retrying in {wait_seconds}s.")
                time.sleep(wait_seconds)
    raise RuntimeError(f"Tender source failed after 3 attempts: {last_error}")


def fetch_notices() -> list[dict]:
    # A 24-hour overlap protects against a delayed or failed scheduled run.
    # SQLite delivery de-duplication prevents an already-sent notice from being
    # repeated when the overlap includes it again.
    lookback_hours = int(os.getenv("TENDERSIGNAL_LOOKBACK_HOURS", "24"))
    updated_to = datetime.now(timezone.utc).replace(microsecond=0)
    updated_from = updated_to - timedelta(hours=lookback_hours)
    params = {
        "limit": "100",
        "stages": "tender",
        "updatedFrom": updated_from.isoformat(),
        "updatedTo": updated_to.isoformat(),
    }
    next_url: str | None = f"{API_URL}?{urlencode(params)}"
    visited_urls: set[str] = set()
    notices: list[dict] = []
    while next_url and next_url not in visited_urls:
        visited_urls.add(next_url)
        package = fetch_package(next_url)
        notices.extend(package.get("releases", []))
        next_url = package.get("links", {}).get("next")
    print(f"Fetched {len(notices)} tender notices across {len(visited_urls)} page(s)")
    return notices


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
    if subscriber.get("match_all", False):
        return 10, ["all tender categories"]
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


def format_deadline(release: dict) -> str:
    value = release.get("tender", {}).get("tenderPeriod", {}).get("endDate")
    if not value:
        return "Not stated"
    try:
        deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{deadline.day} {deadline.strftime('%b %Y, %H:%M UTC')}"
    except ValueError:
        return str(value)


def format_value(release: dict) -> str:
    value = release.get("tender", {}).get("value", {})
    amount, currency = value.get("amount"), value.get("currency", "GBP")
    if amount is None:
        return "Not stated"
    symbol = "£" if currency == "GBP" else f"{currency} "
    try:
        return f"{symbol}{float(amount):,.0f}"
    except (TypeError, ValueError):
        return f"{symbol}{amount}"


def format_location(release: dict) -> str:
    tender = release.get("tender", {})
    values: list[object] = [tender.get("deliveryLocation"), tender.get("deliveryLocations"), tender.get("deliveryAddresses")]
    values.extend(item.get("deliveryAddresses") for item in tender.get("items", []) if isinstance(item, dict))
    labels: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, str) and value.strip():
            labels.append(value.strip())
        elif isinstance(value, dict):
            for key in ("region", "countryName", "locality", "name", "description"):
                if isinstance(value.get(key), str):
                    labels.append(value[key].strip())
            for child in value.values():
                if isinstance(child, (dict, list)):
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for value in values:
        collect(value)
    unique = list(dict.fromkeys(label for label in labels if label))
    return ", ".join(unique[:4]) if unique else "UK location not stated"


def buyer_name(release: dict) -> str:
    return release.get("buyer", {}).get("name") or "Buyer not stated"


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


def digest_sent_today(connection: sqlite3.Connection, subscriber_id: str) -> bool:
    digest_date = datetime.now(timezone.utc).date().isoformat()
    return connection.execute(
        "SELECT 1 FROM digests WHERE subscriber_id = ? AND digest_date = ?", (subscriber_id, digest_date)
    ).fetchone() is not None


def mark_digest_sent(connection: sqlite3.Connection, subscriber_id: str) -> None:
    digest_date = datetime.now(timezone.utc).date().isoformat()
    connection.execute(
        "INSERT OR IGNORE INTO digests(subscriber_id, digest_date, sent_at) VALUES (?, ?, ?)",
        (subscriber_id, digest_date, datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()


def digest_html(notices: list[tuple[dict, int, list[str]]]) -> str:
    items = []
    for notice, relevance, reasons in notices:
        title = notice.get("tender", {}).get("title") or notice.get("title") or "Untitled procurement notice"
        description = notice.get("tender", {}).get("description", "")[:420]
        items.append(
            f"<li><a href=\"{escape(source_link(notice), quote=True)}\">{escape(title)}</a> "
            f"<small>({relevance}/10 · {escape(', '.join(reasons))})</small><br>"
            f"<strong>Buyer:</strong> {escape(buyer_name(notice))} &middot; "
            f"<strong>Deadline:</strong> {escape(format_deadline(notice))} &middot; "
            f"<strong>Value:</strong> {escape(format_value(notice))} &middot; "
            f"<strong>Location:</strong> {escape(format_location(notice))}<br>{escape(description)}</li>"
        )
    return "".join([
        f"<h2>CivicBid daily digest: {len(notices)} new opportunities</h2>",
        "<p>All new open tender notices matching your active all-category profile.</p><ol>",
        "".join(items),
        "</ol><p><small>To stop alerts, reply with the word UNSUBSCRIBE.</small></p>",
    ])


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
    message.set_content("View this CivicBid alert in an HTML-capable email client.")
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
            "CivicBid delivery test",
            "<h2>CivicBid is connected</h2><p>This is a manual delivery test. No customer notice was sent.</p>",
        )
        print(f"Sent delivery test to {recipient}")
        return 0
    subscribers = load_subscribers()
    notices = fetch_notices()
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS deliveries (subscriber_id TEXT, notice_id TEXT, sent_at TEXT, UNIQUE(subscriber_id, notice_id))")
        connection.execute("CREATE TABLE IF NOT EXISTS digests (subscriber_id TEXT, digest_date TEXT, sent_at TEXT, UNIQUE(subscriber_id, digest_date))")
        total_matches = 0
        total_sent = 0
        for subscriber in subscribers:
            if subscriber.get("status", "active") != "active":
                continue
            digest_candidates: list[tuple[dict, int, list[str]]] = []
            for notice in notices:
                notice_id = notice.get("id") or notice.get("ocid")
                if not notice_id or already_sent(connection, subscriber["id"], notice_id):
                    continue
                relevance, reasons = score(notice, subscriber)
                if relevance < subscriber.get("minimum_score", 7):
                    continue
                total_matches += 1
                if subscriber.get("delivery_mode") == "daily_digest":
                    digest_candidates.append((notice, relevance, reasons))
                    continue
                title = notice.get("tender", {}).get("title") or notice.get("title") or "Untitled procurement notice"
                reason_text = ", ".join(reasons) or "profile match"
                html = f"""<h2>CivicBid: {escape(title)}</h2>
<p><strong>Match score:</strong> {relevance}/10 &middot; {escape(reason_text)}</p>
<p>{escape(notice.get('tender', {}).get('description', '')[:1200])}</p>
<p><a href=\"{escape(source_link(notice), quote=True)}\">Open the official notice</a></p>
<p><small>You received this because your company profile is active. To stop alerts, reply with the word UNSUBSCRIBE.</small></p>"""
                send_email(subscriber["email"], f"CivicBid match ({relevance}/10): {title}", html)
                mark_sent(connection, subscriber["id"], notice_id)
                total_sent += 1
                print(f"Sent {notice_id} to {subscriber['email']}")
            if digest_candidates and not digest_sent_today(connection, subscriber["id"]):
                send_email(
                    subscriber["email"],
                    f"CivicBid daily digest: {len(digest_candidates)} opportunities",
                    digest_html(digest_candidates),
                )
                for notice, _, _ in digest_candidates:
                    mark_sent(connection, subscriber["id"], notice.get("id") or notice.get("ocid"))
                mark_digest_sent(connection, subscriber["id"])
                total_sent += 1
                print(f"Sent daily digest with {len(digest_candidates)} notices to {subscriber['email']}")
        print(f"Monitor summary: {total_matches} eligible matches, {total_sent} alerts sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
