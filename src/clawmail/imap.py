"""IMAP connection, fetch, parse, and action execution."""

from __future__ import annotations

import email
import email.message
import email.policy
import email.utils
import imaplib
import logging
import re
import textwrap
import time
from datetime import datetime, timedelta, timezone

from clawmail.models import EmailSummary, ImapConfig

logger = logging.getLogger(__name__)

# Max body snippet length to control token cost
SNIPPET_MAX_CHARS = 500

# Delay between IMAP actions to avoid server rate limits
ACTION_DELAY = 0.1


def _quote_folder(name: str) -> str:
    """Quote an IMAP folder name if it contains spaces."""
    if " " in name and not name.startswith('"'):
        return f'"{name}"'
    return name


def _strip_html(html: str) -> str:
    """Remove HTML tags using stdlib regex. Avoids beautifulsoup dependency."""
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_body(msg: email.message.EmailMessage) -> str:
    """Extract plain text body, falling back to HTML with tag stripping."""
    body = msg.get_body(preferencelist=("plain",))
    if body:
        text = body.get_content()
        if isinstance(text, str):
            return textwrap.shorten(text.strip(), SNIPPET_MAX_CHARS, placeholder="...")

    body = msg.get_body(preferencelist=("html",))
    if body:
        html = body.get_content()
        if isinstance(html, str):
            text = _strip_html(html)
            return textwrap.shorten(text, SNIPPET_MAX_CHARS, placeholder="...")

    return ""


def _parse_email(uid: int, raw_bytes: bytes) -> EmailSummary:
    """Parse raw email bytes into an EmailSummary."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    has_attachments = msg.is_multipart() and any(True for _ in msg.iter_attachments())

    date = None
    date_str = msg.get("Date", "")
    if date_str:
        try:
            date = email.utils.parsedate_to_datetime(date_str)
        except (ValueError, TypeError):
            pass

    return EmailSummary(
        uid=uid,
        message_id=msg.get("Message-ID", ""),
        subject=msg.get("Subject", "(no subject)"),
        sender=msg.get("From", ""),
        date=date,
        snippet=_extract_body(msg),
        has_attachments=has_attachments,
    )


class IMAPClient:
    """IMAP connection manager used as a context manager."""

    def __init__(self, config: ImapConfig, password: str):
        self.host = config.host
        self.port = config.port
        self.email_address = config.email  # pyright: ignore
        self.password = password
        self.trash_folder = config.trash_folder
        self.archive_folder = config.archive_folder
        self._conn: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> IMAPClient:
        self._conn = imaplib.IMAP4_SSL(self.host, self.port)
        self._conn.login(self.email_address, self.password)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    @property
    def conn(self) -> imaplib.IMAP4_SSL:
        if self._conn is None:
            raise RuntimeError("IMAPClient not connected. Use as context manager.")
        return self._conn

    def fetch_recent(
        self,
        mailbox: str = "INBOX",
        days_back: int = 1,
        max_emails: int = 50,
        unread_only: bool = True,
        excluded_uids: set[int] | None = None,
    ) -> list[EmailSummary]:
        """Fetch recent emails. Uses readonly select."""
        self.conn.select(mailbox, readonly=True)

        since_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime(
            "%d-%b-%Y"
        )
        criteria = f"(SINCE {since_date})"
        if unread_only:
            criteria = f"(UNSEEN SINCE {since_date})"

        status, data = self.conn.uid("search", None, criteria)
        if status != "OK" or not data[0]:
            return []

        uids = data[0].split()
        if excluded_uids:
            filtered_uids = []
            for uid_bytes in uids:
                try:
                    uid_int = int(uid_bytes)
                except (TypeError, ValueError):
                    continue
                if uid_int in excluded_uids:
                    continue
                filtered_uids.append(uid_bytes)
            uids = filtered_uids

        if not uids:
            return []

        # Take most recent emails (last N UIDs)
        uids = uids[-max_emails:]

        emails = []
        for uid_bytes in uids:
            uid_str = uid_bytes.decode()
            status, msg_data = self.conn.uid("fetch", uid_str, "(BODY.PEEK[] FLAGS)")
            if status != "OK" or not msg_data:
                continue

            # Response format varies by server: the body is in a tuple part,
            # flags may be in the same tuple's metadata or a separate bytes part.
            raw_email = None
            metadata_parts: list[bytes] = []
            for part in msg_data:
                if isinstance(part, tuple):
                    metadata_parts.append(part[0])
                    raw_email = part[1]
                elif isinstance(part, bytes):
                    metadata_parts.append(part)

            if raw_email is None:
                continue

            uid_int = int(uid_str)
            try:
                summary = _parse_email(uid_int, raw_email)
                combined_metadata = b" ".join(metadata_parts)
                flags_match = re.search(rb"FLAGS \(([^)]*)\)", combined_metadata)
                if flags_match:
                    summary.flags = [
                        f.decode() for f in flags_match.group(1).split() if f
                    ]
                emails.append(summary)
            except Exception as e:
                logger.warning("Failed to parse UID %s: %s", uid_str, e)
                continue

        return emails

    def select_mailbox(self, mailbox: str = "INBOX") -> None:
        """Select a mailbox for read-write operations."""
        self.conn.select(mailbox, readonly=False)

    def execute_action(
        self,
        uid: int,
        action: str,
        target_folder: str | None = None,
    ) -> None:
        """Execute a single action on an email by UID.

        Caller must call select_mailbox() first. Will re-select after
        expunge to keep UID state consistent.
        """
        uid_str = str(uid)

        if action == "flag":
            self.conn.uid("store", uid_str, "+FLAGS", "(\\Flagged)")

        elif action == "trash":
            self.conn.uid("copy", uid_str, _quote_folder(self.trash_folder))
            self.conn.uid("store", uid_str, "+FLAGS", "(\\Deleted)")
            self.conn.expunge()

        elif action == "archive":
            if self.archive_folder:
                self.conn.uid("copy", uid_str, _quote_folder(self.archive_folder))
            self.conn.uid("store", uid_str, "+FLAGS", "(\\Deleted)")
            self.conn.expunge()

        elif action == "move" and target_folder:
            self.conn.uid("copy", uid_str, _quote_folder(target_folder))
            self.conn.uid("store", uid_str, "+FLAGS", "(\\Deleted)")
            self.conn.expunge()

        time.sleep(ACTION_DELAY)

    def list_folders(self) -> list[str]:
        """List all IMAP folders."""
        status, data = self.conn.list()
        if status != "OK":
            return []

        folders = []
        for item in data:
            if item is None:
                continue
            decoded = item.decode() if isinstance(item, bytes) else str(item)
            # Parse folder name from IMAP LIST response: (\\flags) "delimiter" "name"
            match = re.search(r'"[^"]*"\s+"?([^"]+)"?$', decoded)
            if match:
                folders.append(match.group(1))
            else:
                # Fallback: take last space-separated token
                parts = decoded.rsplit(" ", 1)
                if parts:
                    folders.append(parts[-1].strip('"'))
        return sorted(folders)

    def test_connection(self) -> bool:
        """Test the IMAP connection by selecting INBOX."""
        try:
            self.conn.select("INBOX", readonly=True)
            return True
        except Exception as e:
            logger.warning("IMAP connection test failed: %s", e)
            return False
