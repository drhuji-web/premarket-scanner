# -*- coding: utf-8 -*-
"""
PreMarket High-Beta Scanner — v4
يستخدم: Alpha Vantage + Twelve Data + Claude AI
يولّد تقرير HTML تفاعلي يُحفظ محلياً ويُفتح في المتصفح قبل افتتاح السوق
"""

import os
import json
import time
import logging
import requests
import anthropic
import webbrowser
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# ─── إعداد السجل ───────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scanner.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─── مفاتيح API ────────────────────────────────────────────
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
ALPHA_VANTAGE_KEY   = os.environ.get("ALPHA_VANTAGE_KEY", "")
TWELVE_DATA_KEY     = os.environ.get("TWELVE_DATA_KEY", "")

# ─── إعدادات المسح ─────────────────────────────────────────
TOP_N_STOCKS = int(os.environ.get("TOP_N_STOCKS", "7"))
MIN_BETA     = float(os.environ.get("MIN_BETA", "1.5"))

ET = ZoneInfo("America/New_York")

# ─── قائمة بيتا عالية للمسح (نختار منها يومياً) ───────────
# أسهم معروفة بتقلبها العالي — يتم تحديثها بناءً على النتائج
WATCHLIST = [
    # التقنية العالية التقلب
    "NVDA", "AMD", "TSLA", "META", "SMCI", "ARM", "MSTR",
    # البيوتك
    "MRNA", "BNTX", "SAVA", "ACHR", "JOBY",
    # الطاقة والكهرباء
    "PLUG", "FCEL", "ENPH", "SEDG",
    # صغير ومتقلب
    "RIOT", "MARA", "COIN", "HOOD", "SOFI",
    # الاتصالات والترفيه
    "NFLX", "SNAP", "PINS", "RBLX", "U",
    # الفضاء والمستقبل
    "RKLB", "LUNR", "ASTS", "SPCE",
]


# ══════════════════════════════════════════════════════════
#  1. جلب أعلى الرابحين/الخاسرين من Alpha Vantage
# ══════════════════════════════════════════════════════════
def fetch_top_movers_av() -> list[str]:
    """يجلب أعلى المتحركين اليوم من Alpha Vantage"""
    if not ALPHA_VANTAGE_KEY:
        log.warning("ALPHA_VANTAGE_KEY غير موجود — تضطي top movers")
        return []
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=TOP_GAINERS_LOSERS&apikey={ALPHA_VANTAGE_KEY}"
        )
        r = requests.get(url, timeout=15)
        data = r.json()
        gainers = [s["ticker"] for s in data.get("top_gainers", [])[:10]]
        losers  = [s["ticker"] for s in data.get("top_losers", [])[:5]]
        active  = [s["ticker"] for s in data.get("most_actively_traded", [])[:10]]
        symbols = list(dict.fromkeys(gainers + active + losers))
        log.info(f"Alpha Vantage: {len(symbols)} سهم متحرك")
        return symbols
    except Exception as e:
        log.error(f"Alpha Vantage top_movers error: {e}")
        return []


# ══════════════════════════════════════════════════════════
#  2. جلب بيانات السهم من Twelve Data
# ══════════════════════════════════════════════════════════
def fetch_stock_data_twelve(symbol: str) -> dict:
    """يجلب بيانX�ت السهم الأساسية من Twelve Data"""
    if not TWELVE_DATA_KEY:
        return {}
    try:
        # بيانX�ت السعر + الإحصاءات
        url = (
            f"https://api.twelvedata.com/quote"
            f"?symbol={symbol}&apikey={TWELVE_DATA_KEY}"
        )
        r = requests.get(url, timeout=10)
        q = r.json()

        # RSI
        rsi_url = (
            f"https://api.twelvedata.com/rsi"
            f"?symbol={symbol}&interval=1day&time_period=14&apikey={TWELVE_DATA_KEY}"
        )
        rsi_r = requests.get(rsi_url, timeout=10)
        rsi_data = rsi_r.json()
        rsi_val = "N/A"
        if "values" in rsi_data and rsi_data["values"]:
            rsi_val = rsi_data["values"][0].get("rsi", "N/A")

        return {
            "symbol":        symbol,
            "name":          q.get("name", symbol),
            "price":         float(q.get("close", 0) or 0),
            "change":        q.get("percent_change", "0%"),
            "volume":        int(q.get("volume", 0) or 0),
            "avg_volume":    int(q.get("average_volume", 0) or 0),
            "fifty_two_wk_high": q.get("fifty_two_week", {}).get("high", "N/A"),
            "fifty_two_wk_low":  q.get("fifty_two_week", {}).get("low", "N/A"),
            "exchange":      q.get("exchange", ""),
            "rsi":           rsi_val,
        }
    except Exception as e:
        log.debug(f"Twelve Data {symbol}: {e}")
        return {"symbol": symbol}


# ══════════════════════════════════════════════════════════
#  3. جلب البيتا + بيانات الشركة من Alpha Vantage
# ══════════════════════════════════════════════════════════
def fetch_company_overview_av(symbol: str) -> dict:
    """يجلب بيتا وبيانات الشركة من Alpha Vantage"""
    if not ALPHA_VANTAGE_KEY:
        return {}
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=COMPANY_OVERVIEW&symbol={symbol}&apikey={ALPHA_VANTAGE_KEY}"
        )
        r = requests.get(url, timeout=12)
        data = r.json()
        beta = float(data.get("Beta", 0) or 0)
        return {
            "beta":          beta,
            "sector":        data.get("Sector", "N/A"),
            "industry":      data.get("Industry", "N/A"),
            "analyst_target": data.get("AnalystTargetPrice", "N/A"),
            "eps_ttm":       data.get("EPS", "N/A"),
            "pe_ratio":      data.get("PERatio", "N/A"),
            "short_percent": data.get("ShortPercentOutstanding", "N/A"),
        }
    except Exception as e:
        log.debug(f"AV company overview {symbol}: {e}")
        return {}


# ══════════════════════════════════════════════════════════
#  4. جلب الأخبار من Alpha Vantage
# ══════════════════════════════════════════════════════════
def fetch_news_av(symbol: str) -> list[str]:
    """يجلب أحدث الأخبار للسهم"""
    if not ALPHA_VANTAGE_KEY:
        return []
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=NEWS_SENTIMENT&tickers={symbol}"
            f"&limit=5&apikey={ALPHA_VANTAGE_KEY}"
        )
        r = requests.get(url, timeout=12)
        data = r.json()
        headlines = [
            item.get("title", "")
            for item in data.get("feed", [])[:5]
            if item.get("title")
        ]
        return headlines
    except Exception as e:
        log.debug(f"AV news {symbol}: {e}")
        return []


# ══════════════════════════════════════════════════════════
#  5. جلب أرباح قادمة من Alpha Vantage
# ══════════════════════════════════════════════════════════
def fetch_earnings_calendar_av() -> dict[str, str]:
    """يجلب تقويم الأرباح القادمة خلال أسبوعين"""
    if not ALPHA_VANTAGE_KEY:
        return {}
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=EARNINGS_CALENDAR&horizon=3month&apikey={ALPHA_VANTAGE_KEY}"
        )
        r = requests.get(url, timeout=15)
        # الرد CSV
        earnings = {}
        for line in r.text.strip().splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 3:
                sym   = parts[0].strip()
                date  = parts[2].strip()
                earnings[sym] = date
        log.info(f"تقويم الأرباح: {len(earnings)} سهم")
        return earnings
    except Exception as e:
        log.debug(f"AV earnings calendar: {e}")
        return {}


# ══════════════════════════════════════════════════════════
#  6-A. فحص الفجوات السعرية (Gap Analysis)
# ══════════════════════════════════════════════════════════
def fetch_gap_data(symbol: str) -> dict:
    """
    يجلب آخر 60 شمعة يومية ويحسب:
      - فجوة الافتتاح الحالية (gap_today)
      - الفجوات التاريخية غير المغلقة (unfilled_gaps)
    يستخدم Twelve Data TIME_SERIES_DAILY.
    """
    if not TWELVE_DATA_KEY:
        return {}
    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval=1day&outputsize=60"
            f"&apikey={TWELVE_DATA_KEY}"
        )
        r = requests.get(url, timeout=12)
        data = r.json()
        candles = data.get("values", [])
        if len(candles) < 3:
            return {}

        # الشمعات مرتبة من الأحدث للأقدم
        # candles[0] = اليوم أو آخر جلسة
        # candles[1] = الجلسة السابقة
        today_open  = float(candles[0].get("open",  0))
        today_close = float(candles[0].get("close", 0))
        today_high  = float(candles[0].get("high",  0))
        today_low   = float(candles[0].get("low",   0))
        prev_close  = float(candles[1].get("close", 0))
        prev_high   = float(candles[1].get("high",  0))
        prev_low    = float(candles[1].get("low",   0))

        # ── فجوة الافتتاح اليوم ──────────────────────────
        gap_today = None
        if today_open > 0 and prev_close > 0:
            gap_pct = (today_open - prev_close) / prev_close * 100
            if abs(gap_pct) >= 0.5:   # تجاهل الفجوات الصغيرة جداً
                gap_today = {
                    "type":      "gap_up" if gap_pct > 0 else "gap_down",
                    "pct":       round(gap_pct, 2),
                    "from":      prev_close,
                    "to":        today_open,
                    "filled":    False,   # لم تُغلق بعد (افتُتح بفجوة)
                }

        # ── الفجوات التاريخية غير المغلقة ─────────────────
        # نبحث في آخر 50 شمعة (نتخطى [0] و[1])
        unfilled_gaps = []
        for idx in range(2, len(candles) - 1):
            c      = candles[idx]      # الشمعة الحالية في الحلقة
            c_next = candles[idx - 1]  # الشمعة الأحدث منها (الأقرب لليوم)

            c_high  = float(c.get("high",  0))
            c_low   = float(c.get("low",   0))
            cn_open = float(c_next.get("open",  0))
            cn_high = float(c_next.get("high",  0))
            cn_low  = float(c_next.get("low",   0))
            c_date  = c.get("datetime", "")

            # فجوة صعودية: فتح التالية فوق أعلى الحالية
            if cn_open > c_high and (cn_open - c_high) / c_high * 100 >= 0.5:
                gap_top    = cn_open
                gap_bottom = c_high
                # هل أُغلقت؟ (هل وصل السعر لأسفل الفجوة في أي شمعة لاحقة)
                filled = any(
                    float(candles[k].get("low", 999999)) <= gap_bottom
                    for k in range(0, idx - 1)
                )
                if not filled:
                    size_pct = (gap_top - gap_bottom) / gap_bottom * 100
                    unfilled_gaps.append({
                        "type":       "gap_up",
                        "date":       c_date,
                        "gap_bottom": round(gap_bottom, 2),
                        "gap_top":    round(gap_top, 2),
                        "size_pct":   round(size_pct, 2),
                        "distance_pct": round(
                            (gap_bottom - today_close) / today_close * 100, 2
                        ) if today_close else 0,
                    })

            # فجوة هبوطية: فتح التالية تحت أدنى الحالية
            if cn_open < c_low and (c_low - cn_open) / c_low * 100 >= 0.5:
                gap_top    = c_low
                gap_bottom = cn_open
                filled = any(
                    float(candles[k].get("high", 0)) >= gap_top
                    for k in range(0, idx - 1)
                )
                if not filled:
                    size_pct = (gap_top - gap_bottom) / gap_bottom * 100
                    unfilled_gaps.append({
                        "type":       "gap_down",
                        "date":       c_date,
                        "gap_bottom": round(gap_bottom, 2),
                        "gap_top":    round(gap_top, 2),
                        "size_pct":   round(size_pct, 2),
                        "distance_pct": round(
                            (gap_top - today_close) / today_close * 100, 2
                        ) if today_close else 0,
                    })

        # نحتفظ بأقرب 3 فجوات غير مغلقة (الأقرب للسعر الحالي)
        unfilled_gaps.sort(key=lambda g: abs(g["distance_pct"]))
        unfilled_gaps = unfilled_gaps[:3]

        result = {
            "gap_today":     gap_today,
            "unfilled_gaps": unfilled_gaps,
        }
        log.debug(f"  {symbol} gaps → today={gap_today}, unfilled={len(unfilled_gaps)}")
        return result

    except Exception as e:
        log.debug(f"gap_data {symbol}: {e}")
        return {}


def gap_score(gap_data: dict) -> float:
    """
    يٍعب نقطة إضافية للسهم بناءً على الفجوات:
      +2.0  فجوة افتتاح اليوم > 2%
      +1.0  فجوة افتتاح 0.5–2%
      +0.5  لكل فجوة تاريخية غير مغلقة في نطاق ±8% من السعر
    """
    score = 0.0
    gap_t = gap_data.get("gap_today")
    if gap_t:
        pct = abs(gap_t.get("pct", 0))
        score += 2.0 if pct >= 2 else 1.0

    for g in gap_data.get("unfilled_gaps", []):
        if abs(g.get("distance_pct", 99)) <= 8:
            score += 0.5
    return score


# ══════════════════════════════════════════════════════════
#  6-B. البنية العامة للشارت (Chart Structure)
# ══════════════════════════════════════════════════════════
def fetch_chart_structure(symbol: str) -> dict:
    """
    يحلل البنية العامة للشارت من آخر 60 شمعة يومية:
      - الاتجاه العام  (uptrend / downtrend / sideways)
      - مستوى الدعم الرئيسي
      - مستوى المقاومة الرئيسية
      - موقع السعر من المتوسطات (SMA20, SMA50)
      - نمط الشمعة الأخيرة (doji / hammer / engulfing / normal)
    يُعق+ݘ/f��عق+ݘ/Y�ُسل لاتييأت التؗشيئ ويُرسل لـ Claude.
    """
    if not TWEFVE_DATA_KEY:
        return {}
    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval=1day&outputsize=60"
            f"&apikey={TWELVE_DATA_KEY}"
        )
        r = requests.get(url, timeout=12)
        data = r.json()
        candles = data.get("values", [])
        if len(candles) < 20:
            return {}

        closes = [float(c["close"]) for c in candles]
        highs  = [float(c["high"])  for c in candles]
        lows   = [float(c["low"])   for c in candles]

        # الأحدث هو index 0
     price = closes[0]

        # ── المتوسطات المتحركة ──────────────────────────
        sma20 = sum(closes[:20]) / 20
        sma50 = sum(closes[:50]) / 50 if len(closes) >= 50 else None

        # ── الاتجاه العام ───────────────────────────────
        # نقارن متوسط أول 10 شمعات مع متوسط آخر �0 شمعات
        recent_avg = sum(closes[:10])  / 10
        older_avg  = sum(closes[10:20]) / 10
        if recent_avg > older_avg * 1.02:
            trend = "uptrend"
        elif recent_avg < older_avg * 0.98:
            trend = "downtrend"
        else:
            trend = "sideways"

        # ── الدعم والمقاومة (أبسط تعريف: أدنى/أعلى 20 شمعة) ──
        resistance = max(highs[:20])
        support    = min(lows[:20])

        # موقع السعر من الدعم/المقاومة
        dist_resistance_pct = round((resistance - price) / price * 100, 2)
        dist_support_pct    = round((price - support)    / price * 100, 2)

        # ── نمط الشمعة الأخيرة ──────────────────────────
        last = candles[0]
        o = float(last["open"]);  h = float(last["high"])
        l = float(last["low"]);   c = float(last["close"])
        body   = abs(c - o)
        candle_range = h - l
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        if candle_range == 0:
            pattern = "doji"
        elif body / candle_range < 0.1:
            pattern = "doji"
        elif lower_wick > body * 2 and upper_wick < body * 0.5:
            pattern = "hammer"    # إشارة انعكاس صعودي
        elif upper_wick > body * 2 and lower_wick < body * 0.5:
            pattern = "shooting_star"   # إشارة انعكاس هبوطي
        else:
            pattern = "bullish_candle" if c > o else "bearish_candle"

        # هل السعر فوق أو تحت المتوسطات؟
        above_sma20 = price > sma20
        above_sma50 = (price > sma50) if sma50 else None

        return {
            "chart_trend":          trend,
            "sma20":                round(sma20, 2),
            "sma50":                round(sma50, 2) if sma50 else None,
            "above_sma20":          above_sma20,
            "above_sma50":          above_sma50,
            "resistance_20d":       round(resistance, 2),
            "support_20d":          round(support, 2),
            "dist_resistance_pct":  dist_resistance_pct,
            "dist_support_pct":     dist_support_pct,
            "last_candle_pattern":  pattern,
        }

    except Exception as e:
        log.debug(f"chart_structure {symbol}: {e}")
        return {}


# ══════════════════════════════════════════════════════════
#  6-C. الفريمات الكبيرة + كشف النماذج (Multi-Timeframe & Patterns)
# ══════════════════════════════════════════════════════════
def _pivot_highs(prices: list[float], n: int = 2) -> list[tuple[int, float]]:
    """يُعيد قمماً محلية: كل نقطة أعمى من n نقاط على يمينها ويسارها."""
    pivots = []
    for i in range(n, len(prices) - n):
        if all(prices[i] >= prices[i - j] for j in range(1, n + 1)) and \
           all(prices[i] >= prices[i + j] for j in range(1, n + 1)):
            pivots.append((i, prices[i]))
    return pivots


def _pivot_lows(prices: list[float], n: int = 2) -> list[tuple[int, float]]:
    """يُعيد قيعاناً محمية."""
    pivots = []
    for i in range(n, len(prices) - n):
        if all(prices[i] <= prices[i - j] for j in range(1, n + 1)) and \
           all(prices[i] <= prices[i + j] for j in range(1, n + 1)):
            pivots.append((i, prices[i]))
    return pivots


def _detect_patterns(
    closes: list[float],
    highs:  list[float],
    lows:   list[float],
    timeframe: str,
) -> list[dict]:
    """
    يكتشف النماذج الفنية من قائمة شمعات (الأحدث أولاً index=0).
    يعكس القائمة داخلياً لتكون الأقدم أولاً (index=0 = أقدم شمعة)
    وهذا هو الاتجاه الطبيضي للتحليل الرياضي للنماذج.
    يُعيد قائمة نماذج مكتشفة مع مرحلتها.
    """
    # عكس القوائم: الأقدم أوماً
    c = list(reversed(closes))
    h = list(reversed(highs))
    lo = list(reversed(lows))
    n  = len(c)
    if n < 10:
        return []

    patterns = []
    ph = _pivot_highs(h, n=2)   # قمم محلية (index, value)
    pl = _pivot_lows(lo, n=2)   # قيعان محمية

    current_price = c[-1]       # السعر الأحدث

    # ──────────────────────────────────────────────────────
    # 1. Double Top (قمتان متساويتان تقريباً + كسر العنق)
    # ──────────────────────────────────────────────────────
    if len(ph) >= 2:
        p1_i, p1_v = ph[-2]
        p2_i, p2_v = ph[-1]
        if p2_i > p1_i and abs(p1_v - p2_v) / p1_v < 0.03:   # فرق < 3%
            # العنق: أدنى قاع بين القمتين
            neck = min(lo[p1_i:p2_i + 1]) if p2_i > p1_i else lo[p1_i]
            broken = current_price < neck
            patterns.append({
                "name":      "Double Top",
                "name_ar":   "قمة مزدوجة",
                "timeframe": timeframe,
                "stage":     "مكتمل — كسر العنق 🔴" if broken else "يتشكّل ⚠️",
                "bias":      "bearish",
                "key_level": round(neck, 2),
                "note_ar":   f"قمتان عند ~{round(p1_v,2)} | العنق {round(neck,2)}",
            })

    # ──────────────────────────────────────────────────────
    # 2. Double Bottom (قاعان متساويان تقريباً + كسر العنق)
    # ──────────────────────────────────────────────────────
    if len(pl) >= 2:
        b1_i, b1_v = pl[-2]
        b2_i, b2_v = pl[-1]
        if b2_i > b1_i and abs(b1_v - b2_v) / b1_v < 0.03:
            neck = max(h[b1_i:b2_i + 1]) if b2_i > b1_i else h[b1_i]
            broken = current_price > neck
            patterns.append({
                "name":      "Double Bottom",
                "name_ar":   "قاع مزدوج",
                "timeframe": timeframe,
                "stage":     "مكتمل — كسر العنق 🟢" if broken else "يتشكّل ⚠️",
                "bias":      "bullish",
                "key_level": round(neck, 2),
                "note_ar":   f"قاعان عند ~{round(b1_v,2)} | العنق {round(neck,2)}",
            })

    # ──────────────────────────────────────────────────────
    # 3. Head & Shoulders (3 قمم: الوسطى أعمى)
    # ──────────────────────────────────────────────────────
    if len(ph) >= 3:
        ls_i, ls_v = ph[-3]   # الكتف الأيسر
        hd_i, hd_v = ph[-2]   # الرأس
        rs_i, rs_v = ph[-1]   # الكتف الأيمن
        if (hd_v > ls_v * 1.02 and hd_v > rs_v * 1.02
                and abs(ls_v - rs_v) / ls_v < 0.05):
            # العنق: متوسط أدنى قيعان بين القمم
            neckline = (
                min(lo[ls_i:hd_i + 1]) + min(lo[hd_i:rs_i + 1])
            ) / 2 if hd_i > ls_i and rs_i > hd_i else lo[hd_i]
            broken = current_price < neckline
            patterns.append({
                "name":      "Head & Shoulders",
                "name_ar":   "رأس وكتفان",
                "timeframe": timeframe,
                "stage":     "مكتمل — كسر خط العنق 🔴" if broken else "يتشكّل ⚠️",
                "bias":      "bearish",
                "key_level": round(neckline, 2),
                "note_ar":   f"رأس {round(hd_v,2)} | خط العنق ~{round(neckline,2)}",
            })

    # ──────────────────────────────────────────────────────
    # 4. Inverse H&S (3 قيعان: الوسطى أدنى)
    # ──────────────────────────────────────────────────────
    if len(pl) >= 3:
        ls_i, ls_v = pl[-3]
        hd_i, hd_v = pl[-2]
        rs_i, rs_v = pl[-1]
        if (hd_v < ls_v * 0.98 and hd_v < rs_v * 0.98
                and abs(ls_v - rs_v) / ls_v < 0.05):
            neckline = (
                max(h[ls_i:hd_i + 1]) + max(h[hd_i:rs_i + 1])
            ) / 2 if hd_i > ls_i and rs_i > hd_i else h[hd_i]
            broken = current_price > neckline
            patterns.append({
                "name":      "Inverse H&S",
                "name_ar":   "رأس وكتفان معكوس",
                "timeframe": timeframe,
                "stage":     "مكتمل — كسر خط العنق 🟢" if broken else "يتشكّل ⚠️",
                "bias":      "bullish",
                "key_level": round(neckline, 2),
                "note_ar":   f"قاع الرأس {round(hd_v,2)} | خط العنق ~{round(neckline,2)}",
            })

    # ──────────────────────────────────────────────────────
    # 5. Ascending Triangle (قمم ثابتة + قيعان صاعدة)
    # ──────────────────────────────────────────────────────
    if len(ph) >= 2 and len(pl) >= 2:
        top_vals   = [v for _, v in ph[-3:]]
        bot_vals   = [v for _, v in pl[-3:]]
        flat_top   = max(top_vals) - min(top_vals) < max(top_vals) * 0.02
        rising_bot = len(bot_vals) >= 2 and bot_vals[-1] > bot_vals[0] * 1.01
        if flat_top and rising_bot:
            resistance_lvl = sum(top_vals) / len(top_vals)
            broken = current_price > resistance_lvl * 1.005
            patterns.append({
                "name":      "Ascending Triangle",
                "name_ar":   "مثلث صاعد",
                "timeframe": timeframe,
                "stage":     "كسر صعودي 🟢" if broken else "يتشكّل ⚠️",
                "bias":      "bullish",
                "key_level": round(resistance_lvl, 2),
                "note_ar":   f"مقاومة أفقية ~{round(resistance_lvl,2)} | قيعان صاعدة",
            })

    # ──────────────────────────────────────────────────────
    # 6. Descending Triangle (قيعان ثابتة + قمم هابطة)
    # ──────────────────────────────────────────────────────
    if len(ph) >= 2 and len(pl) >= 2:
        top_vals    = [v for _, v in ph[-3:]]
        bot_vals    = [v for _, v in pl[-3:]]
        flat_bot    = max(bot_vals) - min(bot_vals) < max(bot_vals) * 0.02
        falling_top = len(top_vals) >= 2 and top_vals[-1] < top_vals[0] * 0.99
        if flat_bot and falling_top:
            support_lvl = sum(bot_vals) / len(bot_vals)
            broken = current_price < support_lvl * 0.995
            patterns.append({
                "name":      "Descending Triangle",
                "name_ar":   "مثلث هابط",
                "timeframe": timeframe,
                "stage":     "كسر هبوطي 🔴" if broken else "يتشكّل ⚠️",
                "bias":      "bearish",
                "key_level": round(support_lvl, 2),
                "note_ar":   f"دعم أفقي ~{round(support_lvl,2)} | قمم هابطة",
            })

    # ──────────────────────────────────────────────────────
    # 7. Bull Flag (اتجاه صعودي قوي ثم تراجع ضيق)
    # ──────────────────────────────────────────────────────
    if n >= 15:
        pole_gain = (max(h[-15:-5]) - min(lo[-15:-10])) / max(min(lo[-15:-10]), 0.01) * 100
        flag_range = max(h[-5:]) - min(lo[-5:])
        flag_rel   = flag_range / max(c[-5], 0.01) * 100
        if pole_gain > 8 and flag_rel < 4 and c[-1] > c[-5]:
            patterns.append({
                "name":      "Bull Flag",
                "name_ar":   "راية صعودية",
                "timeframe": timeframe,
                "stage":     "يتشكّل — انتظر كسر أعمى الراية ⚠️",
                "bias":      "bullish",
                "key_level": round(max(h[-5:]), 2),
                "note_ar":   f"عمود +{round(pole_gain,1)}% | الراية ضيقة {round(flag_rel,1)}%",
            })

    # ──────────────────────────────────────────────────────
    # 8. Bear Flag (اتجاه هبوطي قوي ثم ارتداد ضيق)
    # ──────────────────────────────────────────────────────
    if n >= 15:
        pole_drop  = (max(h[-15:-5]) - min(lo[-15:-10])) / max(max(h[-15:-5]), 0.01) * 100
        flag_range = max(h[-5:]) - min(lo[-5:])
        flag_rel   = flag_range / max(c[-5], 0.01) * 100
        if pole_drop > 8 and flag_rel < 4 and c[-1] < c[-5]:
            patterns.append({
                "name":      "Bear Flag",
                "name_ar":   "راية هبوطية",
                "timeframe": timeframe,
                "stage":     "يتشكّل — انتظر كسر أسفل الراية ⚠️",
                "bias":      "bearish",
                "key_level": round(min(lo[-5:]), 2),
                "note_ar":   f"عمود -{round(pole_drop,1)}% | الراية ضيقة {round(flag_rel,1)}%",
            })

    # ──────────────────────────────────────────────────────
    # 9. Cup & Handle (كوب دائري + مقبض صغير)
    # ──────────────────────────────────────────────────────
    if n >= 20:
        cup_left  = max(h[:5])
        cup_right = max(h[-5:])
        cup_bot   = min(lo[5:-5])
        depth     = (min(cup_left, cup_right) - cup_bot) / max(cup_bot, 0.01) * 100
        rim_flat  = abs(cup_left - cup_right) / max(cup_left, 0.01) < 0.05
        if depth > 10 and rim_flat:
            handle_range = max(h[-3:]) - min(lo[-3:])
            handle_rel   = handle_range / max(cup_right, 0.01) * 100
            if handle_rel < 5:
                patterns.append({
                    "name":      "Cup & Handle",
                    "name_ar":   "كوب ومقبض",
                    "timeframe": timeframe,
                    "stage":     "يتشكّل — المقبض جاهز ⚠️",
                    "bias":      "bullish",
                    "key_level": round(cup_right, 2),
                    "note_ar":   f"عمق الكوب {round(depth,1)}% | حافة ~{round(cup_right,2)}",
                })

    # ──────────────────────────────────────────────────────
    # 10. Rising / Falling Wedge
    # ──────────────────────────────────────────────────────
    if len(ph) >= 3 and len(pl) >= 3:
        top_slope = ph[-1][1] - ph[-3][1]
        bot_slope = pl[-1][1] - pl[-3][1]
        if top_slope > 0 and bot_slope > 0 and bot_slope > top_slope * 1.2:
            # Wedge صاعد (نقطا الدعم والمقاومة تتقارب صعوداً) — هبوطي عادةً
            patterns.append({
                "name":      "Rising Wedge",
                "name_ar":   "إسفين صاعد",
                "timeframe": timeframe,
                "stage":     "يتشكّل — يترقب كسر هبوطي ⚠️",
                "bias":      "bearish",
                "key_level": round(pl[-1][1], 2),
                "note_ar":   "الدعم والمقاومة يتقاربان صعوداً → ضغط بيع محتمل",
            })
        elif top_slope < 0 and bot_slope < 0 and top_slope < bot_slope * 1.2:
            # Wedge هابط — صعودي عادةً
            patterns.append({
                "name":      "Falling Wedge",
                "name_ar":   "إسفين هابط",
                "timeframe": timeframe,
                "stage":     "يتشكّل — يترقب كسر صعودي ⚠️",
                "bias":      "bullish",
                "key_level": round(ph[-1][1], 2),
                "note_ar":   "الدعم والمقاومة يتقاربان هبوطاً → انعكاس صعودي محتمل",
            })

    return patterns


def fetch_multi_timeframe(symbol: str) -> dict:
    """
    يجلب بيانا֪ الأسبوعي والشهري ويحمل:
      - الاتجاه على كل فريم
      - النماذج الفنية المتشكّلة أو في طور التشكّل
    يُعيد قاموساً يُضاف لبيانا֪ المرشح.
    """
    if not TWELVE_DATA_KEY:
        return {}

    result = {
        "weekly_trend":   None,
        "monthly_trend":  None,
        "tf_alignment":   None,   # هل الاتجاهات متوافقة عبر الفريمات؟
        "patterns":       [],     # كل النماذج المكتشفة من كل الفريمات
        "patterns_summary_ar": "",
    }

    for interval, label, size in [("1week", "أسبوعي", 52), ("1month", "شهري", 24)]:
        try:
            url = (
                f"https://api.twelvedata.com/time_series"
                f"?symbol={symbol}&interval={interval}&outputsize={size}"
                f"&apikey={TWELVE_DATA_KEY}"
            )
            r = requests.get(url, timeout=12)
            data = r.json()
            candles = data.get("values", [])
            if len(candles) < 8:
                continue

            closes = [float(c["close"]) for c in candles]
            highs  = [float(c["high"])  for c in candles]
            lows   = [float(c["low"])   for c in candles]

            # اتجاه الفريم: مقارنة أول 5 شمعا֪ بآخر 5
            recent = sum(closes[:5]) / 5
            older  = sum(closes[-5:]) / 5
            if recent > older * 1.03:
                trend = "uptrend"
            elif recent < older * 0.97:
                trend = "downtrend"
            else:
                trend = "sideways"

            if interval == "1week":
                result["weekly_trend"] = trend
            else:
                result["monthly_trend"] = trend

            # كشف النماذج على هذا الفريم
            detected = _detect_patterns(closes, highs, lows, label)
            result["patterns"].extend(detected)
            time.sleep(0.4)

        except Exception as e:
            log.debug(f"multi_tf {symbol} {interval}: {e}")

    # ── توافق الفريمات ──────────────────────────────────
    w = result["weekly_trend"]
    m = result["monthly_trend"]
    if w and m:
        if w == m == "uptrend":
            result["tf_alignment"] = "bullish_aligned"
        elif w == m == "downtrend":
            result["tf_alignment"] = "bearish_aligned"
        elif w == "uptrend" and m == "downtrend":
            result["tf_alignment"] = "weekly_vs_monthly_conflict"
        else:
            result["tf_alignment"] = "mixed"

    # ── ملخص النماذج بالعربية ──────────────────────────
    if result["patterns"]:
        lines = []
        for p in result["patterns"]:
            lines.append(
                f"• {p['name_ar']} ({p['timeframe']}): {p['stage']} — {p['note_ar']}"
            )
        result["patterns_summary_ar"] = "\n".join(lines)
    else:
        result["patterns_summary_ar"] = "لا نماذج واضحة"

    log.debug(
        f"  {symbol} MTF → W:{result['weekly_trend']} M:{result['monthly_trend']}"
        f" patterns:{len(result['patterns'])}"
    )
    return result


# ══════════════════════════════════════════════════════════
#  6. بناء قائمة الأسهم المرشحة
# ══════════════════════════════════════════════════════════
def build_candidates() -> list[dict]:
    """يجمع البيانات لجميع المرشحين ويصفّيهم"""
    log.info("═══ بدء بناء قائمة المرشحين ═══")

    # دمج قائمة المراقبة مع المتحركين اليوم
    movers  = fetch_top_movers_av()
    symbols = list(dict.fromkeys(movers + WATCHLIST))[:40]

    # جلب تقويم الأرباح مرة واحدة
    earnings_cal = fetch_earnings_calendar_av()
    today_str = datetime.now(ET).strftime("%Y-%m-%d")

    candidates = []
    for sym in symbols:
        log.info(f"  جارٍ تحليل {sym}...")

        # بيانا֪ السعر
        price_data   = fetch_stock_data_twelve(sym)
        company_data = fetch_company_overview_av(sym)

        if not price_data or not company_data:
            time.sleep(0.5)
            continue

        beta  = company_data.get("beta", 0)
        price = price_data.get("price", 0)

        if beta < MIN_BETA or price < 3:
            time.sleep(0.5)
            continue

        # أخبار
        news = fetch_news_av(sym)

        # هل لديه أرباح قريبة؟
        earnings_date = earnings_cal.get(sym, "")
        has_earnings  = bool(earnings_date and earnings_date >= today_str)

        # البنية العامة للشارت + الفجوا֪ السعرية
        gap_data   = fetch_gap_data(sym)
        chart_data = fetch_chart_structure(sym)
        time.sleep(0.3)   # فارق صغير بين الطلبين (نفس الـ API)

        # الفريمات الكبيرة + كشف النماذج الفنية
        mtf_data = fetch_multi_timeframe(sym)
        time.sleep(0.5)

        stock = {
            **price_data,
            **company_data,
            **chart_data,
            "news":                news,
            "earnings_date":       earnings_date if has_earnings else None,
            "has_earnings":        has_earnings,
            "gap_today":           gap_data.get("gap_today"),
            "unfilled_gaps":       gap_data.get("unfilled_gaps", []),
            "_gap_score":          gap_score(gap_data),
            "weekly_trend":        mtf_data.get("weekly_trend"),
            "monthly_trend":       mtf_data.get("monthly_trend"),
            "tf_alignment":        mtf_data.get("tf_alignment"),
            "forming_patterns":    mtf_data.get("patterns", []),
            "patterns_summary_ar": mtf_data.get("patterns_summary_ar", "لا نماذج"),
        }
        candidates.append(stock)
        time.sleep(1.0)   # احترام حدود الطلبات

    # ترتيب مركّب: (بيتا × حجم نسبي) + نقطة الفجوا֪
    candidates.sort(
        key=lambda x: (
            x.get("beta", 0) * (x.get("volume", 1) / max(x.get("avg_volume", 1), 1))
            + x.get("_gap_score", 0)
        ),
        reverse=True,
    )

    log.info(f"إجمالي المرشحين بعد الفلترة: {len(candidates)}")
    return candidates[:20]


# ══════════════════════════════════════════════════════════
#  7. تحليل بالذكاء الاصطهاعي — Claude
# ══════════════════════════════════════════════════════════
def analyze_with_claude(candidates: list[dict]) -> list[dict]:
    """يحلل المرشحين بـ Claude ويختار أفضل TOP_N_STOCKS"""
    log.info("جارٍ التحميل بالذكاء الاصطهاعي...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now(ET).strftime("%A, %B %d, %Y")
    et_now = datetime.now(ET).strftime("%H:%M ET")

    data_str = json.dumps(candidates, ensure_ascii=False, indent=2)

    prompt = f"""You are a professional pre-market stock analyst. Today is {today}, {et_now}.

Analyze these high-beta stock candidates and select the TOP {TOP_N_STOCKS} most likely to make significant price moves at US market open (9:30 AM ET).

Candidate data:
{data_str}

Selection criteria (ranked by importance):
1. Strong pre-market catalyst (earnings, breaking news, analyst action, short squeeze potential)
2. High beta + above-average relative volume (rel_volume > 1.5x)
3. Gap analysis: today's opening gap AND unfilled historical gaps acting as price magnets
4. Chart structure: trend direction, position vs SMA20/SMA50, last candle pattern, distance to support/resistance
5. Multi-timeframe alignment: weekly + monthly trend confirming the daily move
6. Forming chart patterns on weekly/monthly timeframes (huge edge when aligned with daily catalyst)
7. RSI not overbought/oversold unless with strong momentum
8. Risk/reward ratio > 2:1

Gap interpretation rules:
- gap_today "gap_up": bullish momentum — price likely continues up OR gets faded
- gap_today "gap_down": bearish pressure at open
- unfilled_gaps: act as price targets (magnets) — a gap_up below current price = downside target; a gap_down above current price = upside target
- Use dist_resistance_pct / dist_support_pct to judge if the stock is near a key level

Chart structure rules (daily):
- above_sma20=true AND above_sma50=true → strong uptrend context (prefer longs)
- below both SMAs → weak context (prefer shorts or skip)
- last_candle_pattern "hammer" near support → bullish reversal signal
- last_candle_pattern "shooting_star" near resistance → bearish reversal signal
- dist_resistance_pct < 2% → stock is near resistance (harder to run up)
- dist_support_pct < 2% → stock is near support (safer long entry)

Multi-timeframe rules:
- tf_alignment "bullish_aligned" (weekly + monthly both uptrend) → strongest long biaz; increase signal_strength +5
- tf_alignment "bearish_aligned" → avoid longs; reduce signal_strength -5
- tf_alignment "weekly_vs_monthly_conflict" → caution; tighter stops recommended
- weekly_trend "uptrend" + daily catalyst → high-conviction setup

Forming pattern rules (use the patterns list from input data):
- "Ascending Triangle" يتشكّل on weekly → breakout imminent; bias bullish; target = pattern height added to resistance
- "Double Bottom" يتشكّل on weekly/monthly → reversal signal; raise signal_strength +5 if neckline not yet broken
- "Cup & Handle" on weekly → strong continuation signal; entry near handle resistance
- "Rising Wedge" warning → reduce targets by 20%, tighten stop
- "Head & Shoulders" forming → avoid or only short setup
- Completed patterns (مكتمل) carry more weight than forming ones (يتشكّل)
- If patterns list is empty → note "لا نماذج واضحة" in patterns_summary_ar

Respond ONLY with a valid JSON array — no markdown, no explanation outside the JSON:

[
  {{
    "symbol": "TICKER",
    "company": "Company Name",
    "sector": "Sector",
    "price": 142.50,
    "beta": 2.3,
    "change_pct": "+3.2%",
    "rsi": "58",
    "signal_strength": 74,
    "catalysts": ["Strong earnings beat Q3", "Revenue +22% YoY"],
    "catalyst_types": ["earnings", "news"],
    "trend": "bullish",
    "gap_summary": "فجوة صعودية +2.3% عند الافتتاح | فجوة تاريخية غير مغلقة عند $138 (هدف محتمل)",
    "chart_notes": "فوق SMA20 وSMA50 | مقاومة على بُعد 3.1% | نمط مطرقة على الدعم",
    "patterns_summary_ar": "• مثلث صاعد (أسبوعي): يتشكّل ⚠️ — مقاومة أفقية ~$148 | قيعان صاعدة",
    "entry": 143.20,
    "target1": 149.50,
    "target2": 155.00,
    "stop_loss": 139.80,
    "risk_reward": "2.4:1",
    "upside_pct": "+4.4%",
    "analysis_ar": "جملتان أو ثلاث بالعربية تشرح المحفز + البنية الفنية للشارت اليومي والأسبوعي + تأثير الفجوا֪ والنماذج على السيناريو المتوقع."
  }}
]

Rules:
- signal_strength: 55–88 (composite: catalyst strength + chart structure + gap setup + multi-timeframe alignment — NOT a profit probability)
- entry: logical entry considering gap fill risk and nearest support/resistance
- target1: first logical target (gap fill OR resistance level), target2: extended move
- stop_loss: below nearest support or gap fill level, whichever is tighter
- gap_summary: brief Arabic description of today's gap AND nearest unfilled historical gap (or "لا فجوات" if none)
- chart_notes: brief Arabic: SMA position + distance to support/resistance + last candle pattern
- patterns_summary_ar: keep or crefine the detected multi-timeframe patterns from input data; if none detected write "لا نماذج واضحى"
- trend: "bullish" | "bearish" | "neutral" — reflect DAILY timeframe
- catalyst_types: only ["earnings","news","technical","analyst","short_squeeze","gap"]
- All price values must be numbers (not strings)
- analysis_ar: 2-3 sentences in Arabic integrating catalyst + daily/weekly chart structure + gap context + any forming pa