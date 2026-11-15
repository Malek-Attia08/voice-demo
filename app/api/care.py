"""The nightly care for the cloned voices.

Once a night: every voice is looked up at Mistral and a lost one is made again from its kept recording, deletes
Mistral could not do are retried, Mistral is told again to keep every voice 365 days, and the recordings' keys are
locked with the newest master key after a rotation.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from api import library, settings, vault

logger = logging.getLogger(__name__)


async def run_once():
    report = await library.check_all()
    report["deleted"] = await library.retry_pending_deletes()
    report["renewed"] = await library.renew_retention()
    try:
        report["rewrapped"] = vault.rewrap_all()
    except vault.VaultNotConfigured:
        report["rewrapped"] = 0
    logger.info("Voice care: %s", report)
    return report


def seconds_until_next(now, at):
    """Seconds from `now` (timezone-aware) to the next `at` ("HH:MM") in the same zone."""
    hour, minute = (int(part) for part in at.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


if __name__ == "__main__":
    # Run the care once, by hand:  python -m api.care
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(run_once()))


async def loop():
    """Run the care every day at VOICE_CARE_AT (default 00:00) in VOICE_CARE_TIMEZONE (default Brussels time)."""
    zone = ZoneInfo(settings.get("VOICE_CARE_TIMEZONE", "Europe/Brussels"))
    at = settings.get("VOICE_CARE_AT", "00:00")
    while True:
        await asyncio.sleep(seconds_until_next(datetime.now(zone), at))
        try:
            await run_once()
        except Exception:
            logger.exception("Voice care failed; it runs again tomorrow")
