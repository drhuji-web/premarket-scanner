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
MIN_BETA     = float(os.environ.get("MIN_BETA", "1.2"))

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
        log.warning("ALPHA_VANTAGE_KEY غير موجود — تخطي top movers")
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
    """يجلب بيانات السهم الأساسية من Twelve Data"""
    if not TWELVE_DATA_KEY:
        return {}
    try:
        # بيانات السعر + الإحصاءات
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
    يحسب نقطة إضافية للسهم بناءً على الفجوات:
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
    يُعيد قاموساً يُضاف لبيانات الترشيح ويُرسل لـ Claude.
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
        if len(candles) < 20:
            return {}

        closes = [float(c["close"]) for c in candles]
        highs  = [float(c["high"])  for c in candles]
        lows   = [float(c["low"])   for c in candles]

        # الأحدث هو index 0
        price = closes[0]

        # ── المتوسطات المتحركة ──────────────────────────
        sma20 = sum(closes[:20]) / 20
        sma50 = sum(closes[:50]) / 50 if len(closes) >= 50 else None

        # ── الاتجاه العام ───────────────────────────────
        # نقارن متوسط أول 10 شمعات مع متوسط آخر 10 شمعات
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
    """يُعيد قمماً محلية: كل نقطة أعلى من n نقاط على يمينها ويسارها."""
    pivots = []
    for i in range(n, len(prices) - n):
        if all(prices[i] >= prices[i - j] for j in range(1, n + 1)) and \
           all(prices[i] >= prices[i + j] for j in range(1, n + 1)):
            pivots.append((i, prices[i]))
    return pivots


def _pivot_lows(prices: list[float], n: int = 2) -> list[tuple[int, float]]:
    """يُعيد قيعاناً محلية."""
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
    وهذا هو الاتجاه الطبيعي للتحليل الرياضي للنماذج.
    يُعيد قائمة نماذج مكتشفة مع مرحلتها.
    """
    # عكس القوائم: الأقدم أولاً
    c = list(reversed(closes))
    h = list(reversed(highs))
    lo = list(reversed(lows))
    n  = len(c)
    if n < 10:
        return []

    patterns = []
    ph = _pivot_highs(h, n=2)   # قمم محلية (index, value)
    pl = _pivot_lows(lo, n=2)   # قيعان محلية

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
    # 3. Head & Shoulders (3 قمم: الوسطى أعلى)
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
                "stage":     "كسر صاعدق 🟢" if broken else "يتشكّل ⚠️",
                "bias":      "bullish",
                "key_level": round(resistance_lvl, 2),
                "note_ar":   f"مقاومة أفقية ~{round(resistance_lvl,2}} | قيعان صعودة",
            })

    # ──────────────────────────────────────────────────────
    # 6. Descending Triangle (قيعان ثابت٩ + قمم هابط(�
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
                "stage":     "يتشكّل — انتظر كسر أعمل الراية ⚠️",
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
    يجلب بيانات الأسبوعي والشهري ويحلل:
      - الاتجاه على كل فريم
      - النماذج الفنية المتشكّمة أو في طور التشكّل
    يُعيد قاموساً يُضاف لبيانات المرشح.
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

            # اتجاه الفريم: مقارنة أول 5 شمعات بآخر 5
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
#  6-D. الحجم الاستثنائي أثناء الجلسة (Unusual Volume)
# ══════════════════════════════════════════════════════════
def fetch_intraday_volume(symbol: str) -> dict:
    """
    يجلب بيانات الحجم اللحظي ويحسب:
      - rvol_5min : نسبة حجم آخر 5 دقائق ÷ متوسطها التاريخي في نفس الوقت
      - rvol_today: نسبة الحجم المتراكم اليوم ÷ متوسط الحجم اليومي الكامل
      - unusual_volume: True إذا rvol_today > 2.0
      - volume_spike_pct: قمة الحجم الأعلى من المتوسط في آخر 5 دقائق
    يستخدم Twelve Data 1min time_series.
    """
    if not TWELVE_DATA_KEY:
        return {}
    try:
        # آخر 30 شمعة دقيقية
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval=1min&outputsize=30"
            f"&apikey={TWELVE_DATA_KEY}"
        )
        r = requests.get(url, timeout=12)
        data = r.json()
        candles = data.get("values", [])
        if len(candles) < 5:
            return {}

        volumes = [int(c.get("volume", 0) or 0) for c in candles]
        # أحدث 5 دقائق
        recent_5_avg = sum(volumes[:5]) / 5 if volumes[:5] else 0
        # باقي الدقائق كمرجع "طبيعي"
        baseline_avg = sum(volumes[5:]) / max(len(volumes[5:]), 1)

        rvol_5min = round(recent_5_avg / baseline_avg, 2) if baseline_avg > 0 else 0

        # الحجم المتراكم اليوم: نجمع شمعات 1min المتاحة
        today_vol = sum(volumes)

        # نسبة مقارنة بمتوسط يومي (من بيانات السعر)
        # سيتم تحديثها في build_candidates بعد جلب avg_volume
        volume_spike_5m = round(
            (recent_5_avg - baseline_avg) / baseline_avg * 100, 1
        ) if baseline_avg > 0 else 0

        return {
            "rvol_5min":        rvol_5min,
            "intraday_vol_sum": today_vol,
            "volume_spike_5m_pct": volume_spike_5m,
        }
    except Exception as e:
        log.debug(f"intraday_volume {symbol}: {e}")
        return {}


# ══════════════════════════════════════════════════════════
#  6-E. بيانات الخيارات (Options Flow)
# ══════════════════════════════════════════════════════════
def fetch_options_flow(symbol: str) -> dict:
    """
    يجلب بيانات الخيارات من Alpha Vantage HISTORICAL_OPTIONS:
      - unusual_options_volume: True إذا حجم الخيارات اليوم > 2× المتوسط
      - high_vol_oi_ratio: قائمة عقود حجمها > 3× مراكزها المفتوحة ولا تنتهي اليوم
      - options_sentiment: CALL/PUT ratio لقياس توجه السوق
      - smart_money_signal: True إذا وُجدت عقود CALL بعيدة الانتهاء بحجم >> OI
    """
    if not ALPHA_VANTAGE_KEY:
        return {}
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=HISTORICAL_OPTIONS&symbol={symbol}&apikey={ALPHA_VANTAGE_KEY}"
        )
        r = requests.get(url, timeout=15)
        data = r.json()
        options = data.get("data", [])
        if not options:
            return {}

        from datetime import date as dt_date
        today = dt_date.today()
        today_str_opt = today.strftime("%Y-%m-%d")

        total_call_vol = 0
        total_put_vol  = 0
        total_vol      = 0
        high_vol_oi    = []   # عقود: volume >> open_interest وليست اليوم
        smart_signals  = []   # عقود مال ذكي محتمل

        for opt in options:
            try:
                vol   = int(opt.get("volume", 0) or 0)
                oi    = int(opt.get("open_interest", 0) or 0)
                exp   = opt.get("expiration", "")
                ctype = opt.get("type", "").upper()
                strike = float(opt.get("strike", 0) or 0)
                iv    = float(opt.get("implied_volatility", 0) or 0)

                total_vol += vol
                if ctype == "call":
                    total_call_vol += vol
                else:
                    total_put_vol  += vol

                # شرط الفارق الكبير بين الحجم والمراكز المفتوحة
                # + ليست منتهية اليوم (نريد بناء مركز حقيقي)
                if oi > 0 and vol > 0 and exp and exp > today_str_opt:
                    vol_oi_ratio = vol / oi
                    if vol_oi_ratio >= 3.0 and vol >= 500:
                        # احسب كم يوم متبقي
                        try:
                            exp_date = dt_date.fromisoformat(exp)
                            days_left = (exp_date - today).days
                        except Exception:
                            days_left = -1

                        entry = {
                            "type":          ctype,
                            "strike":        strike,
                            "expiration":    exp,
                            "days_to_exp":   days_left,
                            "volume":        vol,
                            "open_interest": oi,
                            "vol_oi_ratio":  round(vol_oi_ratio, 1),
                            "iv":            round(iv * 100, 1) if iv else None,
                        }
                        high_vol_oi.append(entry)

                        # مال ذكي: CALL بعيدة (>14 يوم) + حجم ضخم جداً
                        if ctype == "call" and days_left > 14 and vol_oi_ratio >= 5.0:
                            smart_signals.append(entry)

            except Exception:
                continue

        # ترتيب بحسب نسبة vol/OI الأعلى
        high_vol_oi.sort(key=lambda x: x["vol_oi_ratio"], reverse=True)
        high_vol_oi = high_vol_oi[:5]   # أبرز 5 عقود فقط

        smart_signals.sort(key=lambda x: x["volume"], reverse=True)

        # نسبة CALL/PUT
        cp_ratio = round(total_call_vol / max(total_put_vol, 1), 2)

        # هل الحجم الكلي استثنائي؟ (نقارن بمتوسط تاريخي مبسّط)
        # Alpha Vantage يعيد بيانات آخر يوم تداول فقط في HISTORICAL_OPTIONS
        unusual_options = total_vol >= 5000   # حد بسيط — يمكن تعديله

        result = {
            "options_total_vol":    total_vol,
            "options_call_vol":     total_call_vol,
            "options_put_vol":      total_put_vol,
            "options_cp_ratio":     cp_ratio,
            "unusual_options_vol":  unusual_options,
            "high_vol_oi_contracts": high_vol_oi,
            "smart_money_signals":  smart_signals,
            "has_smart_money":      len(smart_signals) > 0,
        }
        log.debug(
            f"  {symbol} options → total_vol={total_vol} CP={cp_ratio}"
            f" smart={len(smart_signals)} high_ratio_contracts={len(high_vol_oi)}"
        )
        return result

    except Exception as e:
        log.debug(f"options_flow {symbol}: {e}")
        return {}


def options_score(opt: dict) -> float:
    """
    نقطة إضافية للسهم بناءً على إشارات الخيارات:
      +3.0  مال ذكي محتمل (CALL بعيدة + حجم >> OI)
      +2.0  حجم خيارات استثنائي
      +1.0  لكل عقد vol/OI >= 3× وليس اليوم
      +1.0  CP ratio > 2.0 (ميل CALL قوي)
    """
    if not opt:
        return 0.0
    score = 0.0
    if opt.get("has_smart_money"):
        score += 3.0
    if opt.get("unusual_options_vol"):
        score += 2.0
    score += min(len(opt.get("high_vol_oi_contracts", [])), 3) * 1.0
    if opt.get("options_cp_ratio", 1.0) > 2.0:
        score += 1.0
    return score


def options_summary_ar(opt: dict) -> str:
    """يولّد ملخصاً عربياً لبيانات الخيارات."""
    if not opt or opt.get("options_total_vol", 0) == 0:
        return "لا بيانات خىارات"

    lines = []
    cp = opt.get("options_cp_ratio", 1.0)
    total = opt.get("options_total_vol", 0)
    lines.append(
        f"حجم الخيارات: {total:,} | "
        f"Call/Put: {cp:.2f} ({'ميل شراء 🟢' if cp > 1.5 else 'ميل بيع 🔴' if cp < 0.7 else 'متوازن'})"
    )

    contracts = opt.get("high_vol_oi_contracts", [])
    if contracts:
        lines.append("عقود استثنائية (حجم >> مراكز مفتوحة، لا تنتهي اليوم):")
        for c in contracts[:3]:
            emoji = "🟢" if c["type"] == "call" else "🔴"
            lines.append(
                f"  {emoji} {c['type'].upper()} ${c['strike']} "
                f"exp {c['expiration']} ({c['days_to_exp']}ي) | "
                f"Vol {c['volume']:,} / OI {c['open_interest']:,} = {c['vol_oi_ratio']}×"
                + (f" | IV {c['iv']}%" if c.get("iv") else "")
            )

    if opt.get("has_smart_money"):
        sm = opt["smart_money_signals"][0]
        lines.append(
            f"🔔 مال ذكي محتمل: CALL ${sm['strike']} exp {sm['expiration']}"
            f" | {sm['volume']:,} عقد ({sm['vol_oi_ratio']}× OI)"
        )

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  6. بناء قائمة الأسهم المرشحة
# ══════════════════════════════════════════════════════════
def build_candidates() -> list[dict]:
    """يجمع البيانات لجميع المرشحين ويصفّيهم"""
    log.info("═══ بدء بهاء قائمة المرشحين ═══")

    # دمج قائمة المراقبة مع المتحركين اليوم
    movers  = fetch_top_movers_av()
    symbols = list(dict.fromkeys(movers + WATCHLIST))[:40]

    # جلب تقويم الأرباح مرة واحدة
    earnings_cal = fetch_earnings_calendar_av()
    today_str = datetime.now(ET).strftime("%Y-%m-%d")

    candidates = []
    for sym in symbols:
        log.info(f"  جارٍ تحليل {sym}...")

        # بيانات السعر
        price_data = fetch_stock_data_twelve(sym)

        # أسهم الـ WATCHLIST: نضمن دخولها حتى لو API فشل
        in_watchlist = sym in WATCHLIST

        if not price_data or not price_data.get('price', 0):
            if in_watchlist:
                price_data = {"price": 50, "change": "2.0%", "volume": 1000000}
            else:
                time.sleep(0.5)
                continue

        company_data = fetch_company_overview_av(sym)

        # Beta: من API أو تقديرية أو مضمونة للـ WATCHLIST
        beta = float(company_data.get("beta", 0)) if company_data else 0
        price = price_data.get("price", 0)

        if beta == 0:
            try:
                chg_str = str(price_data.get("change", "0%")).replace("%", "").strip()
                daily_chg = abs(float(chg_str))
                beta = round(daily_chg / 1.3, 2)
            except Exception:
                beta = 0

        # WATCHLIST تضمن حد أدنى للبيتا = 1.5
        if in_watchlist and beta < 1.5:
            beta = 1.5

        effective_min_beta = MIN_BETA
        if beta < effective_min_beta or (price < 3 and not in_watchlist):
            time.sleep(0.5)
            continue

        # أخبار
        news = fetch_news_av(sym)

        # هل لديه أرباح قريبة؟
        earnings_date = earnings_cal.get(sym, "")
        has_earnings  = bool(earnings_date and earnings_date >= today_str)

        # البنية العامة للشارت + الفجوات السعرية
        gap_data   = fetch_gap_data(sym)
        chart_data = fetch_chart_structure(sym)
        time.sleep(0.3)   # فارق صغير بين الطلبين (نفس الـ API)

        # الفريمات الكبيرة + كشف النماذج الفنية
        mtf_data = fetch_multi_timeframe(sym)
        time.sleep(0.5)

        # ── الجديد: الحجم اللحظي الاستثنائي ──
        intraday_vol = fetch_intraday_volume(sym)
        # حساب rvol_today = حجم اليوم ÷ متوسط يومي
        avg_vol  = price_data.get("avg_volume", 1) or 1
        today_cumvol = intraday_vol.get("intraday_vol_sum", 0)
        rvol_today = round(today_cumvol / avg_vol, 2) if avg_vol else 0
        intraday_vol["rvol_today"]       = rvol_today
        intraday_vol["unusual_volume"]   = rvol_today >= 2.0
        time.sleep(0.3)

        # ── الجديد: بيانات الخيارات ──
        opt_data    = fetch_options_flow(sym)
        opt_score   = options_score(opt_data)
        opt_summary = options_summary_ar(opt_data)
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
            # ── حجم لحظي استثنائي ──
            "rvol_5min":           intraday_vol.get("rvol_5min", 0),
            "rvol_today":          rvol_today,
            "unusual_volume":      intraday_vol.get("unusual_volume", False),
            "volume_spike_5m_pct": intraday_vol.get("volume_spike_5m_pct", 0),
            # ── خيارات ──
            "options_total_vol":    opt_data.get("options_total_vol", 0),
            "options_cp_ratio":     opt_data.get("options_cp_ratio", 0),
            "unusual_options_vol":  opt_data.get("unusual_options_vol", False),
            "has_smart_money":      opt_data.get("has_smart_money", False),
            "high_vol_oi_contracts": opt_data.get("high_vol_oi_contracts", []),
            "smart_money_signals":  opt_data.get("smart_money_signals", []),
            "_options_score":       opt_score,
            "options_summary_ar":   opt_summary,
        }
        candidates.append(stock)
        time.sleep(1.0)   # احترام حدود الطلبات

    # ترتيب مركّب: (بيتا × حجم نسبي) + نقطة الفجوات + نقطة الخيارات + rvol
    candidates.sort(
        key=lambda x: (
            x.get("beta", 0) * max(x.get("rvol_today", 0),
                                    x.get("volume", 1) / max(x.get("avg_volume", 1), 1))
            + x.get("_gap_score", 0)
            + x.get("_options_score", 0)
        ),
        reverse=True,
    )

    log.info(f"إجمالي المرشحين بعد الفلترة: {len(candidates)}")
    return candidates[:20]


# ══════════════════════════════════════════════════════════
#  7. تحليل بالذكاء الاصطناعي — Claude
# ══════════════════════════════════════════════════════════
def analyze_with_claude(candidates: list[dict]) -> list[dict]:
    """يحلل المرشحين بـ Claude ويختار أفظل TOP_N_STOCKS"""
    log.info("جارٍ التحليل بالذكاء الاصطناعي...")

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
2. High beta + above-average relative volume (rvol_today > 1.5x OR rvol_5min spike > 50%)
3. Gap analysis: today's opening gap AND unfilled historical gaps acting as price magnets
4. Chart structure: trend direction, position vs SMA20/SMA50, last candle pattern, distance to support/resistance
5. Multi-timeframe alignment: weekly + monthly trend confirming the daily move
6. Forming chart patterns on weekly/monthly timeframes (huge edge when aligned with daily catalyst)
7. RSI not overbought/oversold unless with strong momentum
8. Risk/reward ratio > 2:1
9. Unusual options volume AND/OR smart money signals (Vol >> OI on non-today expiry CALL contracts)
10. Options Call/Put ratio skewed bullish (>1.5) confirms directional bias

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
- tf_alignment "bullish_aligned" (weekly + monthly both uptrend) → strongest long bias; increase signal_strength +5
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
    "gap_summary": "فجوة صعودية +2.3% عند الافتتاح | فجوة تاريخية غير مؼلقة عند $138 (هدف محتمل)",
    "chart_notes": "فوق SMA20 وSMA50 | مقاومة على بُعد 3.1% | نمط مطرقة على الدعم",
    "patterns_summary_ar": "• مثلث صاعد (أسبوعي): يتشكّل ⚠️ — مقاومة أفقية ~$148 | قيعان صاعدة",
    "volume_notes": "حجم استثنائي: 2.4× المتوسط | ارتفاع حاد في آخر 5 دقائق +180%",
    "options_notes": "🔔 مال ذكي: CALL $150 exp 2025-02-21 | 3200 عقد (6.2× OI) | CP ratio: 2.8",
    "entry": 143.20,
    "target1": 149.50,
    "target2": 155.00,
    "stop_loss": 139.80,
    "risk_reward": "2.4:1",
    "upside_pct": "+4.4%",
    "analysis_ar": "جملتان أو ثلاث بالضربية تشرح المحفز + البنية الفنية للشارت اليومي والأسبوعي + تأثير الفجوات والنماذج على السيناريو المتوقع + إشارات الحجم والخياراڪ إن وُجدت."
  }}
]

Rules:
- signal_strength: 55–88 (composite: catalyst strength + chart structure + gap setup + multi-timeframe alignment — NOT a profit probability)
- entry: logical entry considering gap fill risk and nearest support/resistance
- target1: first logical target (gap fill OR resistance level), target2: extended move
- stop_loss: below nearest support or gap fill level, whichever is tighter
- gap_summary: brief Arabic description of today's gap AND nearest unfilled historical gap (or "لا فجوات" if none)
- chart_notes: brief Arabic: SMA position + distance to support/resistance + last candle pattern
- patterns_summary_ar: keep or refine the detected multi-timeframe patterns from input data; if none detected write "لا نماذج واضحة"
- trend: "bullish" | "bearish" | "neutral" — reflect DAILY timeframe
- catalyst_types: only ["earnings","news","technical","analyst","short_squeeze","gap","unusual_volume","smart_money"]
- All price values must be numbers (not strings)
- analysis_ar: 2-3 sentences in Arabic integrating catalyst + daily/weekly chart structure + gap context + any forming patterns + unusual volume/options signals if present

Unusual Volume rules:
- rvol_today >= 2.0 → significant intraday volume surge; add "unusual_volume" to catalyst_types; raise signal_strength +3
- rvol_5min spike > 100% → very aggressive volume surge in last 5 min; raise signal_strength +4
- unusual_volume=true with bullish trend → strong confirmation; note in analysis_ar

Smart Money / Options rules:
- has_smart_money=true → institutional positioning detected; add "smart_money" to catalyst_types; raise signal_strength +5
- unusual_options_vol=true → abnormal options activity; raise signal_strength +3
- high_vol_oi_contracts with vol_oi_ratio >= 5 on CALL non-today expiry → highest conviction signal; mention in analysis_ar
- options_cp_ratio > 2.0 → strong call-side bias confirms bullish thesis
- options_cp_ratio < 0.5 → strong put-side bias (bearish signal)
- Include options_summary_ar in the output analysis_ar when meaningful signals exist
"""

    resp = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    result = json.loads(raw)
    log.info(f"Claude اختار {len(result)} أسهم")
    return result


# ══════════════════════════════════════════════════════════
#  8. تنسيق رسالة Telegram
# ══════════════════════════════════════════════════════════
CATALYST_EMOJI = {
    "earnings":      "💰",
    "news":          "📰",
    "technical":     "📊",
    "analyst":       "⭐",
    "short_squeeze": "🚀",
    "gap":           "↕️",
}
TREND_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}

# يوم الأسبوع بالعربية — من datetime مباشرة لا نص ثابت
WEEKDAYS_AR = {
    "Monday": "الاثنين", "Tuesday": "الثلاثاء", "Wednesday": "الأربعاء",
    "Thursday": "الخميس", "Friday": "الجمعة", "Saturday": "السبت", "Sunday": "الأحد",
}

def _esc(text: str) -> str:
    """يُهرّب نص لـ MarkdownV2"""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text

def signal_label(p: int) -> str:
    """قوة الإشارة بدل نسبة احتمالية مضللة"""
    if p >= 75: return "🔥 قوية"
    if p >= 65: return "✅ متوسطة"
    return "⚠️ ضعيفة"


# ══════════════════════════════════════════════════════════
#  جلب سعر BTC الحي
# ══════════════════════════════════════════════════════════
def fetch_btc_price() -> str:
    """يجلب سعر BTC/USD من Alpha Vantage"""
    try:
        if ALPHA_VANTAGE_KEY:
            url = (
                f"https://www.alphavantage.co/query"
                f"?function=CURRENCY_EXCHANGE_RATE"
                f"&from_currency=BTC&to_currency=USD&apikey={ALPHA_VANTAGE_KEY}"
            )
            r = requests.get(url, timeout=10)
            rate = r.json().get("Realtime Currency Exchange Rate", {})
            price = rate.get("5. Exchange Rate", "")
            if price:
                return f"${float(price):,.0f}"
        if TWELVE_DATA_KEY:
            url = (
                f"https://api.twelvedata.com/price"
                f"?symbol=BTC/USD&apikey={TWELVE_DATA_KEY}"
            )
            r = requests.get(url, timeout=10)
            price = r.json().get("price", "")
            if price:
                return f"${float(price):,.0f}"
    except Exception as e:
        log.debug(f"BTC price fetch error: {e}")
    return "N/A"


_TREND_AR = {
    "uptrend":   "📈 صاعد",
    "downtrend": "📉 هابط",
    "sideways":  "➡️ عرضي",
}
_ALIGN_AR = {
    "bullish_aligned":          "✅ توافق صعودي",
    "bearish_aligned":          "🔴 توافق هبوطي",
    "weekly_vs_monthly_conflict": "⚠️ تعارض أسبوعي/شهري",
    "mixed":                    "🔄 مختلط",
}


def _tf_line(s: dict) -> str:
    """يولّد سطراً ملخّصاً للفريمات الكبيرة للعرض في التقرير."""
    w = _TREND_AR.get(s.get("weekly_trend", ""), "")
    m = _TREND_AR.get(s.get("monthly_trend", ""), "")
    al = _ALIGN_AR.get(s.get("tf_alignment", ""), "")
    parts = []
    if w:
        parts.append(f"أسبوعي: {w}")
    if m:
        parts.append(f"شهري: {m}")
    if al:
        parts.append(al)
    return " | ".join(parts) if parts else "لا بيانات"


def format_message(stocks: list[dict]) -> str:
    now_et  = datetime.now(ET)
    et_now  = now_et.strftime("%I:%M %p ET")
    date_ar = now_et.strftime("%Y/%m/%d")
    weekday_en = now_et.strftime("%A")
    weekday_ar = WEEKDAYS_AR.get(weekday_en, weekday_en)
    btc_price  = fetch_btc_price()

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "📈 *تقرير ما قبل الافتتاح*",
        f"🕐 {_esc(et_now)}  \\|  📅 {_esc(date_ar)} {_esc(weekday_ar)}",
        f"₿ BTC: {_esc(btc_price)}  \\|  🎯 أفضل {len(stocks)} أسهم",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for i, s in enumerate(stocks, 1):
        trend_e    = TREND_EMOJI.get(s.get("trend", "neutral"), "🟡")
        cat_emojis = " ".join(CATALYST_EMOJI.get(ct, "•") for ct in s.get("catalyst_types", []))
        cats_text  = _esc(" | ".join(s.get("catalysts", [])))
        prob       = s.get("signal_strength", s.get("probability", 0))
        analysis   = _esc(s.get("analysis_ar", ""))

        lines += [
            f"*{i}\\. {s['symbol']}* — {_esc(s.get('company',''))}",
            f"   {trend_e} β\\={_esc(str(s.get('beta','?')))}  \\|  {_esc(s.get('change_pct','?'))}  \\|  RSI {_esc(str(s.get('rsi','?')))}",
            f"   💡 قوة الإشارة: {signal_label(prob)}  \\|  🏭 {_esc(s.get('sector',''))}",
            "",
            f"   {cat_emojis} *المحفزات:*",
            f"   _{cats_text}_",
            "",
            f"   📊 *الشارت:* _{_esc(s.get('chart_notes',''))}_",
            f"   ↕️ *الفجوات:* _{_esc(s.get('gap_summary','لا فجوات'))}_",
            f"   🗓 *الفريمات:* _{_esc(_tf_line(s))}_",
            f"   📐 *نماذج:* _{_esc(s.get('patterns_summary_ar','لا نماذج'))}_",
            *(
                [f"   📈 *حجم استثنائي:* _{_esc(s.get('volume_notes',''))}_"]
                if s.get("unusual_volume") or s.get("volume_notes")
                else []
            ),
            *(
                [f"   ⚡ *خيارات:* _{_esc(s.get('options_notes','') or s.get('options_summary_ar',''))}_"]
                if s.get("unusual_options_vol") or s.get("has_smart_money")
                else []
            ),
            "",
            f"   📌 *خطة التداول:*",
            f"   🔵 دخول:   `{s.get('entry','?')}`",
            f"   🟢 هدف 1:  `{s.get('target1','?')}` \\({_esc(s.get('upside_pct','?'))}\\)",
            f"   🟩 هدف 2:  `{s.get('target2','?')}`",
            f"   🔴 وقف:    `{s.get('stop_loss','?')}`",
            f"   ⚖️ ع\\/م:    {_esc(s.get('risk_reward','?'))}",
            "",
            f"   🤖 *تحليل AI:*",
            f"   {analysis}",
            "",
            "  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
            "",
        ]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ *تنبيه:* هذا التقرير لأغراض تعليمية فقط\\.",
        "التداول ينطوي على مخاطر\\. استشر مستشارك المالي\\.",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 _PreMarket AI Scanner v2_",
    ]

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  9. إرسال Telegram
# ══════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════
#  9. توليد تقرير HTML تفاعلي
# ══════════════════════════════════════════════════════════
_BQPS_COLOR = {
    "bullish_aligned":            "#16a34a",
    "bearish_aligned":            "#dc2626",
    "weekly_vs_monthly_conflict": "#d97706",
    "mixed":                      "#6b7280",
}

def generate_html_report(stocks: list[dict], btc_price: str) -> str:
    """يولّد تقرير HTML تفاعلي كامل — يُحفظ محلياً."""
    now_et     = datetime.now(ET)
    date_str   = now_et.strftime("%A, %B %d, %Y")
    time_str   = now_et.strftime("%I:%M %p ET")
    weekday_ar = WEEKDAYS_AR.get(now_et.strftime("%A"), "")

    def trend_ar(t):
        return {"uptrend": "📈 صاعد", "downtrend": "📉 هابط", "sideways": "➡️ عرضي"}.get(t or "", "—")

    def align_ar(a):
        return {
            "bullish_aligned":            "✅ توافق صعودي",
            "bearish_aligned":            "🔴 توافق هبوطي",
            "weekly_vs_monthly_conflict": "⚠️ تعارض أسبوعي/شهري",
            "mixed":                      "🔄 مختلط",
        }.get(a or "", "—")

    def trend_emoji(t):
        return {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(t or "", "🟡")

    def sig_badge(p):
        if p >= 75: return '<span class="badge fire">🔥 قوية</span>'
        if p >= 65: return '<span class="badge ok">✅ متوسطة</span>'
        return '<span class="badge warn">⚠️ ضعيفة</span>'

    cards_html = ""
    for i, s in enumerate(stocks, 1):
        sym      = s.get("symbol", "?")
        company  = s.get("company", "")
        sector   = s.get("sector", "")
        price    = s.get("price", "?")
        beta     = s.get("beta", "?")
        chg      = s.get("change_pct", "?")
        rsi      = s.get("rsi", "?")
        sig      = s.get("signal_strength", s.get("probability", 0))
        cats     = " | ".join(s.get("catalysts", []))
        chart_n  = s.get("chart_notes", "—")
        gap_s    = s.get("gap_summary", "لا فجوات")
        patt_s   = s.get("patterns_summary_ar", "لا نماذج")
        w_trend  = trend_ar(s.get("weekly_trend"))
        m_trend  = trend_ar(s.get("monthly_trend"))
        align    = align_ar(s.get("tf_alignment"))
        align_c  = _BIAS_COLOR.get(s.get("tf_alignment", ""), "#6b7280")
        entry    = s.get("entry", "?")
        t1       = s.get("target1", "?")
        t2       = s.get("target2", "?")
        sl       = s.get("stop_loss", "?")
        rr       = s.get("risk_reward", "?")
        up       = s.get("upside_pct", "?")
        analysis = s.get("analysis_ar", "")
        te       = trend_emoji(s.get("trend"))

        # ── الحجم الاستثنائي ──
        vol_notes    = s.get("volume_notes", "")
        rvol_today   = s.get("rvol_today", 0)
        rvol_5min    = s.get("rvol_5min", 0)
        unusual_vol  = s.get("unusual_volume", False)
        spike_5m     = s.get("volume_spike_5m_pct", 0)
        vol_color    = "#22c55e" if unusual_vol else "#94a3b8"
        rvol_label   = f"RVOL اليوم: <strong style='color:{vol_color}'>{rvol_today}×</strong>"
        if rvol_5min:
            rvol_label += f" | آخر 5د: <strong>{rvol_5min}×</strong>"
        if spike_5m and spike_5m > 0:
            rvol_label += f" | ارتفاع حجم 5د: +{spike_5m}%"
        if unusual_vol:
            rvol_label += " 🔥"

        # ── بيانات الخيارات ──
        opt_notes    = s.get("options_notes", "")
        opt_summary  = s.get("options_summary_ar", "")
        opt_total    = s.get("options_total_vol", 0)
        cp_ratio     = s.get("options_cp_ratio", 0)
        unusual_opt  = s.get("unusual_options_vol", False)
        smart_money  = s.get("has_smart_money", False)
        high_contracts = s.get("high_vol_oi_contracts", [])

        # بناء قسم الخيارات
        if opt_total > 0 or opt_notes:
            cp_color = "#22c55e" if cp_ratio > 1.5 else "#ef4444" if cp_ratio < 0.7 else "#94a3b8"
            cp_dir   = "ميل شراء" if cp_ratio > 1.5 else "ميل بيع" if cp_ratio < 0.7 else "متوازن"
            opt_header = (
                f"حجم: {opt_total:,} | "
                f"<span style='color:{cp_color}'>C/P: {cp_ratio:.2f} ({cp_dir})</span>"
            ) if opt_total else opt_notes

            contracts_html = ""
            for c in high_contracts[:3]:
                c_em = "🟢" if c.get("type") == "call" else "🔴"
                iv_str = f" | IV {c['iv']}%" if c.get("iv") else ""
                contracts_html += (
                    f"<div class='opt-contract'>"
                    f"{c_em} <strong>{c.get('type','').upper()}</strong> "
                    f"${c.get('strike',0)} &nbsp;exp {c.get('expiration','')} "
                    f"({c.get('days_to_exp','')}ي) &nbsp;|&nbsp; "
                    f"Vol <strong>{c.get('volume',0):,}</strong> / "
                    f"OI {c.get('open_interest',0):,} = "
                    f"<strong style='color:#f59e0b'>{c.get('vol_oi_ratio',0)}×</strong>"
                    f"{iv_str}</div>"
                )

            sm_badge = ""
            if smart_money:
                sm_signals = s.get("smart_money_signals", [])
                if sm_signals:
                    sm = sm_signals[0]
                    sm_badge = (
                        f"<div class='smart-badge'>🔔 مال ذكي: CALL ${sm.get('strike',0)} "
                        f"exp {sm.get('expiration','')} | {sm.get('volume',0):,} عقد "
                        f"({sm.get('vol_oi_ratio',0)}× OI)</div>"
                    )

            options_section = f"""
    <div class="options-block{'  smart-money' if smart_money else ''}">
      <h4>⚡ إشارات الخيارات{'  &nbsp;🔔 مال ذكي' if smart_money else ''}</h4>
      <p class="opt-header">{opt_header}</p>
      {contracts_html}
      {sm_badge}
    </div>"""
        else:
            options_section = ""

        cards_html += f"""
<div class="card" id="stock-{i}">
  <div class="card-header">
    <div class="card-rank">#{i}</div>
    <div class="card-title">
      <span class="symbol">{sym}</span>
      <span class="company">{company}</span>
    </div>
    <div class="card-meta">
      {te} β={beta} &nbsp;|&nbsp; {chg} &nbsp;|&nbsp; RSI {rsi}
    </div>
    <div class="card-sig">{sig_badge(sig)}</div>
  </div>

  <div class="card-body">
    <div class="row2">
      <div class="col">
        <h4>📣 المحفزات</h4>
        <p class="cats">{cats}</p>
        <p class="sector">🏭 {sector}</p>
        <p class="rvol-line">{rvol_label}</p>
      </div>
      <div class="col">
        <h4>📌 خطة التداول</h4>
        <table class="trade-table">
          <tr><td>🔵 دخول</td>  <td><code>{entry}</code></td></tr>
          <tr><td>🟢 هدف 1</td> <td><code>{t1}</code> <span class="up">({up})</span></td></tr>
          <tr><td>🟩 هدف 2</td> <td><code>{t2}</code></td></tr>
          <tr><td>🔴 وقف</td>   <td><code>{sl}</code></td></tr>
          <tr><td>⚖️ ع/م</td>   <td><strong>{rr}</strong></td></tr>
        </table>
      </div>
    </div>

    <div class="row3">
      <div class="info-block">
        <h4>📊 الشارت اليومي</h4>
        <p>{chart_n}</p>
      </div>
      <div class="info-block">
        <h4>↕️ الفجوات السعرية</h4>
        <p>{gap_s}</p>
      </div>
      <div class="info-block tf-block" style="border-color:{align_c}">
        <h4>🗓 الفريمات الكبيرة</h4>
        <p>أسبوعي: {w_trend} &nbsp;|&nbsp; شهري: {m_trend}</p>
        <p class="align-tag" style="color:{align_c}">{align}</p>
      </div>
    </div>

    {options_section}

    <div class="patterns-block">
      <h4>📐 النماذج الفنية المتشكّلة</h4>
      <div class="patterns-text">{patt_s.replace(chr(10), "<br>")}</div>
    </div>

    <div class="analysis-block">
      <h4>🤖 تحميل AI</h4>
      <p class="analysis-text">{analysis}</p>
    </div>
  </div>
</div>
"""

    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PreMarket Scanner — {date_str}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      direction: rtl;
      padding: 20px;
    }}
    .header {{
      background: linear-gradient(135deg, #1e3a5f 0%, #0f2027 100%);
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 20px 28px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .header h1 {{ font-size: 1.5rem; color: #38bdf8; }}
    .header .meta {{ font-size: 0.9rem; color: #94a3b8; line-height: 1.8; text-align: left; }}
    .header .btc {{ font-size: 1.1rem; color: #f59e0b; font-weight: bold; }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      margin-bottom: 20px;
      overflow: hidden;
      transition: box-shadow 0.2s;
    }}
    .card:hover {{ box-shadow: 0 4px 20px rgba(56,189,248,0.15); }}
    .card-header {{
      background: #0f2027;
      padding: 14px 20px;
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
      border-bottom: 1px solid #334155;
    }}
    .card-rank {{
      font-size: 1.4rem;
      font-weight: bold;
      color: #38bdf8;
      min-width: 36px;
    }}
    .card-title {{ flex: 1; }}
    .symbol {{ font-size: 1.4rem; font-weight: bold; color: #f1f5f9; margin-left: 8px; }}
    .company {{ font-size: 0.85rem; color: #94a3b8; }}
    .card-meta {{ font-size: 0.9rem; color: #cbd5e1; }}
    .badge {{
      padding: 4px 12px;
      border-radius: 20px;
      font-size: 0.82rem;
      font-weight: bold;
    }}
    .badge.fire {{ background: #7f1d1d; color: #fca5a5; }}
    .badge.ok   {{ background: #14532d; color: #86efac; }}
    .badge.warn {{ background: #713f12; color: #fde68a; }}
    .card-body {{ padding: 18px 20px; }}
    .row2 {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-bottom: 16px;
    }}
    @media (max-width: 600px) {{ .row2 {{ grid-template-columns: 1fr; }} }}
    h4 {{
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #64748b;
      margin-bottom: 8px;
    }}
    .cats {{ color: #e2e8f0; font-size: 0.92rem; line-height: 1.5; }}
    .sector {{ color: #94a3b8; font-size: 0.82rem; margin-top: 6px; }}
    .trade-table {{ width: 100%; border-collapse: collapse; }}
    .trade-table td {{
      padding: 5px 8px;
      font-size: 0.9rem;
      border-bottom: 1px solid #1e293b;
    }}
    .trade-table td:first-child {{ color: #94a3b8; width: 70px; }}
    code {{
      background: #0f172a;
      padding: 2px 8px;
      border-radius: 4px;
      color: #38bdf8;
      font-family: monospace;
    }}
    .up {{ color: #4ade80; font-size: 0.82rem; }}
    .row3 {{
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 14px;
      margin-bottom: 14px;
    }}
    @media (max-width: 700px) {{ .row3 {{ grid-template-columns: 1fr; }} }}
    .info-block {{
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 12px 14px;
      font-size: 0.88rem;
      line-height: 1.55;
    }}
    .tf-block {{ border-width: 2px; }}
    .align-tag {{ font-weight: bold; margin-top: 4px; }}
    .patterns-block {{
      background: #0f172a;
      border: 1px solid #4c1d95;
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 14px;
      font-size: 0.88rem;
      line-height: 1.7;
    }}
    .patterns-text {{ color: #c4b5fd; }}
    .analysis-block {{
      background: #0f2027;
      border-right: 3px solid #38bdf8;
      padding: 12px 16px;
      border-radius: 0 8px 8px 0;
    }}
    .analysis-text {{
      color: #bae6fd;
      font-size: 0.92rem;
      line-height: 1.7;
    }}
    .rvol-line {{
      margin-top: 8px;
      font-size: 0.85rem;
      color: #94a3b8;
    }}
    .options-block {{
      background: #0d1f35;
      border: 1px solid #1e40af;
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 14px;
      font-size: 0.87rem;
      line-height: 1.7;
    }}
    .options-block.smart-money {{
      border-color: #f59e0b;
      box-shadow: 0 0 10px rgba(245,158,11,0.25);
    }}
    .options-block h4 {{ color: #60a5fa; margin-bottom: 6px; }}
    .opt-header {{ color: #94a3b8; margin-bottom: 6px; }}
    .opt-contract {{
      background: #1e293b;
      border-radius: 6px;
      padding: 5px 10px;
      margin: 4px 0;
      color: #cbd5e1;
      font-size: 0.84rem;
    }}
    .smart-badge {{
      background: linear-gradient(90deg,#78350f,#451a03);
      border: 1px solid #f59e0b;
      border-radius: 6px;
      padding: 6px 12px;
      margin-top: 8px;
      color: #fcd34d;
      font-weight: bold;
      font-size: 0.88rem;
    }}
    .footer {{
      text-align: center;
      color: #475569;
      font-size: 0.78rem;
      margin-top: 30px;
      padding-top: 16px;
      border-top: 1px solid #1e293b;
    }}
  </style>
</head>
<body>

<div class="header">
  <div>
    <h1>📈 PreMarket Scanner</h1>
    <p style="color:#94a3b8;font-size:0.88rem;margin-top:4px">
      أفظل {len(stocks)} أسهم مرتاد لافتتاح السوق الأمريكي
    </p>
  </div>
  <div class="meta">
    <div>📅 {weekday_ar} {date_str}</div>
    <div>🕐 {time_str}</div>
    <div class="btc">₿ BTC: {btc_price}</div>
  </div>
</div>

{cards_html}

<div class="footer">
  ⚠️ هذا التقرير لأغراض تعليمية وتحليلية فقط — ليس توصية استثمارية<br>
  PreMarket AI Scanner v4 — Alpha Vantage + Twelve Data + Claude AI
</div>

</body>
</html>"""
    return html


def save_report(html: str) -> Path:
    """يحفظ التقرير كملف HTML ويُعيد مساره."""
    out_dir = Path(__file__).parent / "reports"
    out_dir.mkdir(exist_ok=True)
    date_tag = datetime.now(ET).strftime("%Y-%m-%d")
    path = out_dir / f"premarket_{date_tag}.html"
    path.write_text(html, encoding="utf-8")
    log.info(f"✅ تم حفظ التقرير: {path}")
    return path


# ══════════════════════════════════════════════════════════
#  10. نقطة الدخول
# ══════════════════════════════════════════════════════════
def run(open_browser: bool = True):
    log.info("═══ PreMarket Scanner v4 — بدء التشغيل ═══")

    # التحقق من المفاتيح المطلوبة
    missing = [k for k, v in {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    }.items() if not v]
    if missing:
        log.error(f"مفاتيح مفقودة: {', '.join(missing)}")
        return

    if not ALPHA_VANTAGE_KEY and not TWELVE_DATA_KEY:
        log.error("يجب وجود ALPHA_VANTAGE_KEY أو TWELVE_DATA_KEY على الأقل")
        return

    # 1. جمع البيانات
    candidates = build_candidates()
    if not candidates:
        log.error("لا يوجد مرتاد — اصنهاء")
        return

    # 2. تحليل AI
    analyzed = analyze_with_claude(candidates)

    # 3. سعر BTC الحي
    btc = fetch_btc_price()

    # 4. توليد وحفظ HTML
    html = generate_html_report(analyzed, btc)
    report_path = save_report(html)

    # 5. فتح في المتصفح تلقائياً
    if open_browser:
        webbrowser.open(report_path.as_uri())
        log.info("🌐 تم فتح التقرير في المتصفح")

    print(f"\n{'═'*50}")
    print(f"✅ التقرير جاهز: {report_path}")
    print(f"{'═'*50}\n")
    log.info("═══ اكتمل التشغيل ═══")
    return report_path


if __name__ == "__main__":
    run()
