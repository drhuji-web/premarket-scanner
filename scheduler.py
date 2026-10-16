"""
مُجدوِل تلقائي
يشغّل السكانر يومياً 8:30 AM ET (أيام العمل فقط)
"""

import schedule
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

from scanner import run

log = logging.getLogger(__name__)
ET  = ZoneInfo("America/New_York")

def is_weekday() -> bool:
    return datetime.now(ET).weekday() < 5

def job():
    if is_weekday():
        log.info("يوم عمل ✅ — تشغيل المسح")
        run()
    else:
        log.info("عطلة نهاية أسبوع — تم التخطي")

# 8:30 AM توقيت نيويورك = قبل الافتتاح بساعة كاملة
schedule.every().day.at("08:30").do(job)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    now_et = datetime.now(ET).strftime("%I:%M %p ET")
    log.info(f"🚀 المُجدوِل يعمل — اموقت الآن: {now_et}")
    log.info("   التقرير يُرسَل يومياً 8:30 AM ET (الاثنين–الجمعة)")

    while True:
        schedule.run_pending()
        time.sleep(30)
