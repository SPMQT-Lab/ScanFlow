"""Watch a shared folder for new screenshots, push each to Slack or Telegram.

Runs on the DEV PC. Polls the folder where the lab-PC monitor drops its
PNGs (via SMB share, USB drive, OneDrive, whatever), and forwards each
new image to your chosen messenger as it arrives. Already-sent files are
tracked in a JSON state file so the relay survives restarts without
re-spamming you.

Configuration is via environment variables — pick one channel and set
its three vars (token, target). The relay auto-detects which channel
to use based on what's set.

  Slack:
      $env:SLACK_BOT_TOKEN  = "xoxb-..."        # bot user token
      $env:SLACK_CHANNEL    = "C0123456789"     # channel ID (not name)
      pip install slack_sdk

  Telegram:
      $env:TELEGRAM_TOKEN   = "123:ABC..."      # @BotFather token
      $env:TELEGRAM_CHAT_ID = "12345678"        # chat id (use @userinfobot)
      # pure requests — no extra install

WhatsApp is intentionally not included — see README for why and what to
use instead (Twilio sandbox, paid).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("scanflow.relay")


# ---------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------

def slack_uploader(token: str, channel: str):
    """Returns a function(path, caption) -> bool."""
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        log.error("slack_sdk not installed. Run: pip install slack_sdk")
        sys.exit(2)
    client = WebClient(token=token)

    def upload(path: Path, caption: str) -> bool:
        try:
            client.files_upload_v2(
                channel=channel,
                file=str(path),
                title=path.name,
                initial_comment=caption,
            )
            return True
        except SlackApiError as e:
            log.warning("Slack upload failed for %s: %s", path.name, e.response["error"])
            return False
    return upload


def telegram_uploader(token: str, chat_id: str):
    import requests
    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    def upload(path: Path, caption: str) -> bool:
        try:
            with open(path, "rb") as fh:
                r = requests.post(
                    url,
                    data={"chat_id": chat_id, "caption": caption},
                    files={"photo": (path.name, fh, "image/png")},
                    timeout=30,
                )
            if r.status_code == 200 and r.json().get("ok"):
                return True
            log.warning("Telegram returned %s: %s", r.status_code, r.text[:200])
            return False
        except Exception as e:
            log.warning("Telegram upload failed for %s: %s", path.name, e)
            return False
    return upload


def pick_backend():
    slack_tok = os.environ.get("SLACK_BOT_TOKEN")
    slack_ch = os.environ.get("SLACK_CHANNEL")
    tg_tok = os.environ.get("TELEGRAM_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if slack_tok and slack_ch:
        log.info("using Slack backend (channel %s)", slack_ch)
        return slack_uploader(slack_tok, slack_ch)
    if tg_tok and tg_chat:
        log.info("using Telegram backend (chat %s)", tg_chat)
        return telegram_uploader(tg_tok, tg_chat)
    log.error(
        "No credentials found.\n"
        "Set either:\n"
        "  SLACK_BOT_TOKEN + SLACK_CHANNEL,  or\n"
        "  TELEGRAM_TOKEN + TELEGRAM_CHAT_ID"
    )
    sys.exit(2)


# ---------------------------------------------------------------------
# State + main loop
# ---------------------------------------------------------------------

class SentState:
    """Tracks which files have already been forwarded."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._sent: set[str] = set()
        if path.exists():
            try:
                self._sent = set(json.loads(path.read_text()))
            except Exception:
                log.warning("Could not parse %s — starting fresh", path)

    def has(self, name: str) -> bool:
        return name in self._sent

    def mark(self, name: str) -> None:
        self._sent.add(name)
        try:
            self._path.write_text(json.dumps(sorted(self._sent), indent=2))
        except Exception as e:
            log.warning("Could not write %s: %s", self._path, e)


def caption_for(path: Path) -> str:
    """Build a short caption from the filename stem (YYYYMMDDTHHMMSSZ_kind)."""
    stem = path.stem
    parts = stem.split("_", 1)
    if len(parts) == 2:
        stamp_raw, kind = parts
        try:
            from datetime import datetime
            t = datetime.strptime(stamp_raw, "%Y%m%dT%H%M%SZ")
            return f"{kind} · {t:%Y-%m-%d %H:%M UTC}"
        except ValueError:
            pass
    return path.name


def relay_once(folder: Path, sent: SentState, upload) -> int:
    """Send any new PNGs. Returns the number sent in this pass."""
    if not folder.exists():
        log.warning("folder does not exist (yet?): %s", folder)
        return 0
    files = sorted(folder.glob("*.png"))
    n = 0
    for p in files:
        if sent.has(p.name):
            continue
        cap = caption_for(p)
        ok = upload(p, cap)
        if ok:
            sent.mark(p.name)
            log.info("sent %s", p.name)
            n += 1
        else:
            log.info("will retry next cycle: %s", p.name)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Forward lab screenshots to Slack / Telegram.")
    ap.add_argument("--folder", type=Path,
                    default=Path(r"\\LABPC\ScanflowMonitor\screenshots"),
                    help="Shared folder containing PNGs to forward")
    ap.add_argument("--state", type=Path, default=Path("relay_state.json"),
                    help="JSON file remembering which images have been sent")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="Seconds between scans (default 60)")
    ap.add_argument("--once", action="store_true",
                    help="Process current backlog once and exit")
    ap.add_argument("--catch-up", action="store_true",
                    help="On first run, mark *all* existing files as already sent "
                         "so you don't get a flood. Only sends files that appear "
                         "after the relay starts.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    upload = pick_backend()
    sent = SentState(args.state)

    if args.catch_up:
        for p in sorted(args.folder.glob("*.png") if args.folder.exists() else []):
            sent.mark(p.name)
        log.info("catch-up: marked %d existing files as sent", len(list(args.folder.glob('*.png'))))

    if args.once:
        n = relay_once(args.folder, sent, upload)
        log.info("done: %d sent", n)
        return 0

    log.info("watching %s every %.0fs (Ctrl-C to stop)", args.folder, args.interval)
    try:
        while True:
            relay_once(args.folder, sent, upload)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
