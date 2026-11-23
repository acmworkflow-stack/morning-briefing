from __future__ import annotations

"""Local morning market newsletter for personal research.

Run with the Python environment that has pandas and yfinance installed. The
program refreshes one self-contained HTML report; it does not place trades.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import webbrowser
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf


ROOT = Path(__file__).resolve().parent
DEFAULT_CSV = ROOT / "data" / "mnq_yahoo_5m.csv"
DEFAULT_REPORT = ROOT / "morning_brief.html"
LOCAL_TZ = ZoneInfo("America/Chicago")
EASTERN = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0 (compatible; PersonalMorningBrief/1.0)"

# Edit these lists to tailor the daily read. Yahoo symbols are used for quotes.
WATCHLIST: list[tuple[str, str, str]] = [
    ("MNQ=F", "Micro Nasdaq futures", "Index futures"),
    ("ES=F", "S&P 500 futures", "Index futures"),
    ("RTY=F", "Russell 2000 futures", "Index futures"),
    ("YM=F", "Dow futures", "Index futures"),
    ("GC=F", "Gold futures", "Cross-market"),
    ("CL=F", "WTI crude futures", "Cross-market"),
    ("QQQ", "Nasdaq 100 ETF", "Benchmarks"),
    ("SPY", "S&P 500 ETF", "Benchmarks"),
    ("SMH", "Semiconductor ETF", "Benchmarks"),
    ("IWM", "Russell 2000 ETF", "Benchmarks"),
    ("^VIX", "CBOE volatility index", "Risk context"),
    ("^TNX", "U.S. 10-year yield index", "Risk context"),
    ("DX-Y.NYB", "U.S. dollar index", "Risk context"),
    ("NVDA", "NVIDIA", "Nasdaq leaders"),
    ("MSFT", "Microsoft", "Nasdaq leaders"),
    ("AAPL", "Apple", "Nasdaq leaders"),
    ("AMZN", "Amazon", "Nasdaq leaders"),
    ("META", "Meta Platforms", "Nasdaq leaders"),
    ("GOOGL", "Alphabet", "Nasdaq leaders"),
    ("AVGO", "Broadcom", "Nasdaq leaders"),
    ("AMD", "AMD", "Nasdaq leaders"),
    ("TSLA", "Tesla", "Nasdaq leaders"),
]

COMPANY_CIK = {
    "NVDA": "0001045810", "MSFT": "0000789019", "AAPL": "0000320193",
    "AMZN": "0001018724", "META": "0001326801", "GOOGL": "0001652044",
    "AVGO": "0001730168", "AMD": "0000002488", "TSLA": "0001318605",
}

YAHOO_NEWS_SYMBOLS = ["MNQ=F", "QQQ", "NVDA", "MSFT", "AAPL", "AMZN", "META", "AVGO", "AMD"]
EARNINGS_SYMBOLS = ["NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL", "AVGO", "AMD", "TSLA"]

BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_ICS = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


@dataclass
class CalendarEvent:
    when: datetime
    title: str
    source: str
    url: str
    impact: str = "High"


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def local_day_label(value: datetime) -> str:
    return f"{value.strftime('%a %b')} {value.day}"


def local_clock_label(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def load_local_env() -> None:
    """Read simple KEY=VALUE settings without requiring python-dotenv."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


def request_text(url: str, timeout: int = 15, headers: dict[str, str] | None = None) -> str:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT, **(headers or {})})
    response.raise_for_status()
    return response.text


def latest_daily_quotes() -> dict[str, dict[str, Any]]:
    symbols = [row[0] for row in WATCHLIST]
    try:
        frame = yf.download(
            tickers=symbols,
            period="10d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            actions=False,
            threads=True,
            progress=False,
            timeout=20,
        )
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    if frame is None or frame.empty:
        return result

    for symbol, name, group in WATCHLIST:
        try:
            if isinstance(frame.columns, pd.MultiIndex):
                if (symbol, "Close") in frame.columns:
                    close = frame[(symbol, "Close")].dropna()
                    volume = frame[(symbol, "Volume")].reindex(close.index) if (symbol, "Volume") in frame.columns else None
                elif ("Close", symbol) in frame.columns:
                    close = frame[("Close", symbol)].dropna()
                    volume = frame[("Volume", symbol)].reindex(close.index) if ("Volume", symbol) in frame.columns else None
                else:
                    continue
            else:
                close = frame["Close"].dropna()
                volume = frame["Volume"].reindex(close.index) if "Volume" in frame else None
            if close.empty:
                continue
            last = safe_float(close.iloc[-1])
            prev = safe_float(close.iloc[-2]) if len(close) > 1 else None
            first = safe_float(close.iloc[0])
            if last is None:
                continue
            result[symbol] = {
                "symbol": symbol, "name": name, "group": group, "close": last,
                "day_pct": ((last / prev) - 1.0) * 100 if prev else None,
                "period_pct": ((last / first) - 1.0) * 100 if first else None,
                "date": pd.Timestamp(close.index[-1]).date(),
                "volume": safe_float(volume.iloc[-1]) if volume is not None and len(volume) else None,
            }
        except Exception:
            continue
    return result


def yahoo_news() -> list[dict[str, Any]]:
    # Yahoo's RSS endpoint is convenient for a personal digest but is not a
    # guaranteed developer API. Fail softly and leave source links visible.
    symbols = ",".join(YAHOO_NEWS_SYMBOLS)
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(symbols, safe=',=^.-')}&region=US&lang=en-US"
    try:
        body = request_text(url, timeout=12)
        root = ET.fromstring(body)
        items: list[dict[str, Any]] = []
        for node in root.findall(".//item"):
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            pub = (node.findtext("pubDate") or "").strip()
            source = (node.findtext("source") or "Yahoo Finance").strip()
            if not title or not link:
                continue
            try:
                published = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
            except (TypeError, ValueError, OverflowError):
                published = None
            items.append({"title": title, "url": link, "source": source, "published": published})
        # De-duplicate titles and prioritize newer stories.
        unique: dict[str, dict[str, Any]] = {}
        for item in items:
            unique.setdefault(item["title"].casefold(), item)
        return sorted(unique.values(), key=lambda x: x["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:18]
    except Exception as exc:
        return [{"error": f"Yahoo headlines unavailable right now ({type(exc).__name__})."}]


def parse_ics(text: str, source: str, url: str, start: datetime, end: datetime) -> list[CalendarEvent]:
    # RFC 5545 folded lines continue with one leading space or tab.
    text = re.sub(r"\r?\n[ \t]", "", text)
    events: list[CalendarEvent] = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, flags=re.S):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.upper()] = value.strip()
        title = fields.get("SUMMARY", "Scheduled release")
        title = title.replace(r"\,", ",").replace(r"\;", ";").replace(r"\n", " ")
        start_key = next((key for key in fields if key.startswith("DTSTART")), None)
        if not start_key:
            continue
        raw = fields[start_key]
        try:
            if re.fullmatch(r"\d{8}", raw):
                event_time = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=EASTERN)
            elif raw.endswith("Z"):
                event_time = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            else:
                parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S") if len(raw) >= 15 else datetime.strptime(raw, "%Y%m%dT%H%M")
                tz_match = re.search(r"TZID=([^;:]+)", start_key)
                tz_name = tz_match.group(1) if tz_match else "America/New_York"
                event_time = parsed.replace(tzinfo=ZoneInfo(tz_name))
            event_time = event_time.astimezone(LOCAL_TZ)
            if start <= event_time <= end:
                events.append(CalendarEvent(event_time, title, source, url))
        except (ValueError, KeyError):
            continue
    return events


class FedCalendarParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_h4 = False
        self.year: int | None = None
        self.sections: dict[int, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "h4":
            self.in_h4 = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "h4":
            self.in_h4 = False

    def handle_data(self, data: str) -> None:
        match = re.search(r"(20\d{2}) FOMC Meetings", data)
        if self.in_h4 and match:
            self.year = int(match.group(1))
        elif self.year is not None:
            self.sections.setdefault(self.year, []).append(data.strip())


def fomc_events(now: datetime, horizon_days: int = 30) -> list[CalendarEvent]:
    try:
        parser = FedCalendarParser()
        parser.feed(request_text(FOMC_URL, timeout=15))
        events: list[CalendarEvent] = []
        month_numbers = {name: number for number, name in enumerate(
            ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1
        )}
        limit = now + timedelta(days=horizon_days)
        for year, parts in parser.sections.items():
            text = " ".join(part for part in parts if part)
            for match in re.finditer(r"\b(" + "|".join(month_numbers) + r")\s+(\d{1,2})\s*[-–]\s*(\d{1,2})\*?", text):
                month, first_day, last_day = match.groups()
                try:
                    begin = datetime(year, month_numbers[month], int(first_day), 0, 0, tzinfo=EASTERN).astimezone(LOCAL_TZ)
                    finish_date = date(year, month_numbers[month], int(last_day))
                    if now <= begin <= limit:
                        events.append(CalendarEvent(begin, f"FOMC meeting ({begin.strftime('%b')} {begin.day}-{finish_date.day})", "Federal Reserve", FOMC_URL))
                except ValueError:
                    continue
        return events
    except Exception:
        return []


def event_calendar(days: int = 14) -> tuple[list[CalendarEvent], list[str]]:
    now = datetime.now(LOCAL_TZ)
    end = now + timedelta(days=days)
    events: list[CalendarEvent] = []
    notes: list[str] = []
    for label, url, source, keep in [
        ("BLS", BLS_ICS, "BLS", re.compile(r"Consumer Price Index|Employment Situation|Producer Price Index|Job Openings and Labor Turnover|Employment Cost Index|Productivity and Costs", re.I)),
        ("BEA", BEA_ICS, "BEA", re.compile(r"Gross Domestic Product|Personal Income and Outlays", re.I)),
    ]:
        try:
            pulled = parse_ics(request_text(url, timeout=15), source, url, now, end)
            events.extend(event for event in pulled if keep.search(event.title))
        except Exception as exc:
            notes.append(f"{label} schedule could not refresh ({type(exc).__name__}); use its official calendar link.")
    events.extend(fomc_events(now, days))
    if not any(event.source == "Federal Reserve" for event in events):
        notes.append("FOMC dates could not refresh; use the Federal Reserve calendar link.")
    events.extend(upcoming_earnings(now, days))
    unique: dict[tuple[str, str], CalendarEvent] = {}
    for event in events:
        unique[(event.when.isoformat(), event.title)] = event
    return sorted(unique.values(), key=lambda event: event.when), notes


def upcoming_earnings(now: datetime, days: int) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []
    stop = now + timedelta(days=days)
    for symbol in EARNINGS_SYMBOLS:
        try:
            data = yf.Ticker(symbol).get_earnings_dates(limit=6)
            if data is None or data.empty:
                continue
            for index, row in data.iterrows():
                stamp = pd.Timestamp(index)
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize(EASTERN)
                when = stamp.to_pydatetime().astimezone(LOCAL_TZ)
                if now <= when <= stop:
                    events.append(CalendarEvent(when, f"{symbol} earnings (estimated)", "Yahoo Finance", f"https://finance.yahoo.com/quote/{symbol}/analysis/"))
                    break
        except Exception:
            continue
    return events


def sec_filings() -> tuple[list[dict[str, Any]], str | None]:
    contact = os.getenv("SEC_USER_AGENT", "").strip()
    if not contact:
        return [], "Set SEC_USER_AGENT in your environment to enable SEC filing updates (include your contact email)."
    headers = {"User-Agent": contact, "Accept-Encoding": "gzip, deflate"}
    cutoff = date.today() - timedelta(days=14)
    rows: list[dict[str, Any]] = []
    for ticker, cik in COMPANY_CIK.items():
        try:
            response = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers, timeout=12)
            response.raise_for_status()
            recent = response.json().get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            docs = recent.get("primaryDocument", [])
            for idx, form in enumerate(forms):
                if form not in {"10-K", "10-Q", "8-K"} or idx >= len(dates):
                    continue
                filing_day = date.fromisoformat(dates[idx])
                if filing_day < cutoff:
                    continue
                accession = accessions[idx].replace("-", "")
                doc = quote(docs[idx])
                url = f"{SEC_ARCHIVES}/{int(cik)}/{accession}/{doc}"
                rows.append({"ticker": ticker, "form": form, "date": filing_day, "url": url})
                break
            time.sleep(0.12)  # Stay comfortably below SEC's published rate cap.
        except Exception:
            continue
    return rows, None


def fred_context() -> tuple[list[dict[str, Any]], str | None]:
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return [], "FRED series are optional: set a free FRED_API_KEY to show the latest CPI, fed funds rate, and 10-year Treasury series."
    series = [("DGS10", "10-year Treasury yield", "percent"), ("DFF", "Federal funds effective rate", "percent"), ("CPIAUCSL", "CPI index", "index"), ("UNRATE", "Unemployment rate", "percent")]
    out: list[dict[str, Any]] = []
    for series_id, label, unit in series:
        try:
            response = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": series_id, "api_key": api_key, "file_type": "json", "sort_order": "desc", "limit": 13},
                timeout=12,
            )
            response.raise_for_status()
            observations = [item for item in response.json().get("observations", []) if item.get("value") not in {".", None}]
            if not observations:
                continue
            latest = float(observations[0]["value"])
            prior = float(observations[1]["value"]) if len(observations) > 1 else None
            yoy = ((latest / float(observations[12]["value"])) - 1) * 100 if series_id == "CPIAUCSL" and len(observations) >= 13 else None
            out.append({"id": series_id, "label": label, "value": latest, "prior": prior, "date": observations[0]["date"], "unit": unit, "yoy": yoy})
        except Exception:
            continue
    return out, None


def mnq_session_context(csv_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"available": False, "path": str(csv_path)}
    if not csv_path.exists():
        result["message"] = "The local MNQ 5-minute file was not found."
        return result
    try:
        frame = pd.read_csv(csv_path)
        if "timestamp" not in frame or not {"open", "high", "low", "close"}.issubset(frame.columns):
            result["message"] = "The MNQ file does not have the expected timestamp/open/high/low/close columns."
            return result
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dt.tz_convert(LOCAL_TZ)
        frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
        if frame.empty:
            result["message"] = "The MNQ file contains no valid candles."
            return result
        frame["local_date"] = frame["timestamp"].dt.date
        latest_stamp = frame["timestamp"].iloc[-1].to_pydatetime()
        weekdays = sorted(set(frame.loc[(frame.timestamp.dt.hour >= 8) & (frame.timestamp.dt.hour < 15), "local_date"]))
        rth_rows = pd.DataFrame()
        rth_date = None
        for day in reversed(weekdays):
            # In Chicago, regular cash hours begin at 08:30. Exclude the 15:00 close.
            mask = (frame.local_date == day) & (((frame.timestamp.dt.hour > 8) | ((frame.timestamp.dt.hour == 8) & (frame.timestamp.dt.minute >= 30)))) & (frame.timestamp.dt.hour < 15)
            candidate = frame.loc[mask]
            if not candidate.empty:
                rth_rows, rth_date = candidate, day
                break
        if rth_date is None:
            result["message"] = "No recent New York cash-session candles were found in the local MNQ file."
            return result
        asia_start = datetime.combine(rth_date - timedelta(days=1), dt_time(18, 0), LOCAL_TZ)
        asia_end = datetime.combine(rth_date, dt_time(2, 0), LOCAL_TZ)
        asia_rows = frame[(frame.timestamp >= asia_start) & (frame.timestamp < asia_end)]
        volumes = pd.to_numeric(rth_rows.get("volume", pd.Series(0, index=rth_rows.index)), errors="coerce").fillna(0)
        typical = (rth_rows.high + rth_rows.low + rth_rows.close) / 3
        vwap = float((typical * volumes).sum() / volumes.sum()) if volumes.sum() > 0 else None
        rth_range = float(rth_rows.high.max() - rth_rows.low.min())
        rth_open, rth_close = float(rth_rows.open.iloc[0]), float(rth_rows.close.iloc[-1])
        # Compare the latest RTH range with prior sessions in the local dataset.
        daily_ranges = []
        for day, group in frame.groupby("local_date"):
            if day > rth_date:
                continue
            g = group[((group.timestamp.dt.hour > 8) | ((group.timestamp.dt.hour == 8) & (group.timestamp.dt.minute >= 30))) & (group.timestamp.dt.hour < 15)]
            if len(g) >= 20:
                daily_ranges.append((day, float(g.high.max() - g.low.min())))
        prior_ranges = [value for day, value in daily_ranges if day < rth_date][-20:]
        median_range = float(pd.Series(prior_ranges).median()) if prior_ranges else None
        freshness_hours = max(0.0, (datetime.now(LOCAL_TZ) - latest_stamp).total_seconds() / 3600)
        result.update({
            "available": True, "rth_date": rth_date, "rth_open": rth_open, "rth_close": rth_close,
            "rth_change": rth_close - rth_open, "rth_range": rth_range, "rth_vwap": vwap,
            "asia_date": rth_date - timedelta(days=1), "asia_range": float(asia_rows.high.max() - asia_rows.low.min()) if not asia_rows.empty else None,
            "asia_open": float(asia_rows.open.iloc[0]) if not asia_rows.empty else None,
            "asia_close": float(asia_rows.close.iloc[-1]) if not asia_rows.empty else None,
            "median_range": median_range, "prior_range_count": len(prior_ranges),
            "latest_stamp": latest_stamp, "freshness_hours": freshness_hours,
        })
        return result
    except Exception as exc:
        result["message"] = f"Could not read the local MNQ file ({type(exc).__name__})."
        return result


def fmt_price(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def fmt_money(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _statement_value(frame: pd.DataFrame, labels: tuple[str, ...], period: Any) -> float | None:
    if frame.empty or period not in frame.columns:
        return None
    for label in labels:
        if label in frame.index:
            value = safe_float(frame.at[label, period])
            if value is not None:
                return value
    return None


def _grade_score(metric: str, value: float | None) -> float | None:
    if value is None:
        return None
    if metric == "current_ratio":
        return max(0.0, min(100.0, value * 40.0))
    if metric == "quick_ratio":
        return max(0.0, min(100.0, value * 70.0))
    if metric == "debt_equity":
        return max(0.0, min(100.0, 100.0 - value * 35.0))
    if metric == "net_debt_ebitda":
        return max(0.0, min(100.0, 100.0 - max(0.0, value) * 18.0))
    if metric == "interest_coverage":
        return max(0.0, min(100.0, value * 20.0))
    if metric == "roic":
        return max(0.0, min(100.0, (value + 0.05) * 400.0))
    if metric == "revenue_growth":
        return max(0.0, min(100.0, (value + 0.2) * 250.0))
    if metric == "fcf_conversion":
        return max(0.0, min(100.0, value * 80.0))
    if metric == "gross_margin":
        return max(0.0, min(100.0, value * 1.6))
    if metric == "operating_margin":
        return max(0.0, min(100.0, (value + 0.05) * 300.0))
    if metric == "net_margin":
        return max(0.0, min(100.0, (value + 0.05) * 300.0))
    if metric == "fcf_margin":
        return max(0.0, min(100.0, (value + 0.05) * 300.0))
    return None


def financial_health_data(query: str) -> dict[str, Any]:
    """Search Yahoo Finance and assemble reported-quarter company metrics."""
    result: dict[str, Any] = {"query": query, "available": False}
    try:
        matches = yf.Search(query, max_results=10).quotes
        equities = [row for row in matches if str(row.get("quoteType", "")).upper() == "EQUITY"]
        if not equities:
            result["message"] = "No listed-company match was found. Try a stock ticker such as MSFT or a company name such as Microsoft."
            return result
        match = equities[0]
        symbol = str(match.get("symbol", "")).strip()
        company = str(match.get("longname") or match.get("shortname") or symbol)
        ticker = yf.Ticker(symbol)
        income = ticker.quarterly_income_stmt
        balance = ticker.quarterly_balance_sheet
        cashflow = ticker.quarterly_cashflow
        if income.empty and balance.empty and cashflow.empty:
            result.update(symbol=symbol, company=company, message="Yahoo Finance returned no quarterly statements for this company.")
            return result

        periods = sorted(set(income.columns) | set(balance.columns) | set(cashflow.columns))[-12:]
        rows: list[dict[str, Any]] = []
        ebitda_history: list[float | None] = []
        for period in periods:
            revenue = _statement_value(income, ("Total Revenue", "Operating Revenue", "Revenue"), period)
            gross_profit = _statement_value(income, ("Gross Profit",), period)
            operating_income = _statement_value(income, ("Operating Income", "Operating Income Loss", "Total Operating Income As Reported"), period)
            net_income = _statement_value(income, ("Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation Net Minority Interest"), period)
            ebitda = _statement_value(income, ("EBITDA", "Normalized EBITDA"), period)
            ebit = _statement_value(income, ("EBIT", "Operating Income", "Total Operating Income As Reported"), period)
            interest_expense = _statement_value(income, ("Interest Expense", "Interest Expense Non Operating"), period)
            pretax_income = _statement_value(income, ("Pretax Income",), period)
            tax_provision = _statement_value(income, ("Tax Provision",), period)
            ebitda_history.append(ebitda)
            current_assets = _statement_value(balance, ("Current Assets",), period)
            current_liabilities = _statement_value(balance, ("Current Liabilities",), period)
            total_debt = _statement_value(balance, ("Total Debt",), period)
            net_debt = _statement_value(balance, ("Net Debt",), period)
            cash = _statement_value(balance, ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents And Short Term Investments"), period)
            cash_only = _statement_value(balance, ("Cash And Cash Equivalents",), period)
            short_investments = _statement_value(balance, ("Other Short Term Investments", "Short Term Investments"), period)
            receivables = _statement_value(balance, ("Accounts Receivable", "Accounts Receivable Net Current"), period)
            equity = _statement_value(balance, ("Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity"), period)
            fcf = _statement_value(cashflow, ("Free Cash Flow",), period)
            if fcf is None:
                operating_cash = _statement_value(cashflow, ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"), period)
                capex = _statement_value(cashflow, ("Capital Expenditure", "Capital Expenditures"), period)
                if operating_cash is not None and capex is not None:
                    fcf = operating_cash + capex if capex < 0 else operating_cash - capex
            rows.append({
                "period": period,
                "current_ratio": current_assets / current_liabilities if current_assets is not None and current_liabilities else None,
                "quick_ratio": (cash_only + (short_investments or 0.0) + (receivables or 0.0)) / current_liabilities if cash_only is not None and current_liabilities else None,
                "debt_equity": total_debt / equity if total_debt is not None and equity is not None and equity > 0 else None,
                "net_debt_ebitda": None,
                "interest_coverage": None,
                "roic": None,
                "revenue_growth": None,
                "fcf_conversion": None,
                "gross_margin": gross_profit / revenue if gross_profit is not None and revenue else None,
                "operating_margin": operating_income / revenue if operating_income is not None and revenue else None,
                "net_margin": net_income / revenue if net_income is not None and revenue else None,
                "fcf_margin": fcf / revenue if fcf is not None and revenue else None,
                "free_cash_flow": fcf,
                "revenue": revenue,
                "net_income": net_income,
                "ebit": ebit,
                "interest_expense": interest_expense,
                "pretax_income": pretax_income,
                "tax_provision": tax_provision,
                "equity": equity,
                "total_debt": total_debt,
                "net_debt": net_debt if net_debt is not None else (total_debt - cash if total_debt is not None and cash is not None else None),
                "cash": cash,
            })
        for index, row in enumerate(rows):
            trailing_ebitda = ebitda_history[max(0, index - 3):index + 1]
            if len(trailing_ebitda) == 4 and all(value is not None for value in trailing_ebitda):
                ttm_ebitda = sum(value for value in trailing_ebitda if value is not None)
                if ttm_ebitda > 0 and row["net_debt"] is not None:
                    row["net_debt_ebitda"] = row["net_debt"] / ttm_ebitda
            if index >= 3:
                trailing = rows[index - 3:index + 1]
                ttm_ebit = [item["ebit"] for item in trailing]
                ttm_interest = [item["interest_expense"] for item in trailing]
                ttm_net_income = [item["net_income"] for item in trailing]
                ttm_fcf = [item["free_cash_flow"] for item in trailing]
                ttm_pretax = [item["pretax_income"] for item in trailing]
                ttm_tax = [item["tax_provision"] for item in trailing]
                if all(value is not None for value in ttm_ebit) and all(value is not None for value in ttm_interest):
                    interest_paid = sum(abs(value) for value in ttm_interest if value is not None)
                    if interest_paid > 0:
                        row["interest_coverage"] = sum(value for value in ttm_ebit if value is not None) / interest_paid
                if all(value is not None for value in ttm_net_income) and all(value is not None for value in ttm_fcf):
                    trailing_net_income = sum(value for value in ttm_net_income if value is not None)
                    if trailing_net_income > 0:
                        row["fcf_conversion"] = sum(value for value in ttm_fcf if value is not None) / trailing_net_income
                if index >= 4 and row["revenue"] is not None and rows[index - 4]["revenue"]:
                    row["revenue_growth"] = row["revenue"] / rows[index - 4]["revenue"] - 1.0
                if (all(value is not None for value in ttm_ebit) and all(value is not None for value in ttm_pretax)
                        and all(value is not None for value in ttm_tax) and row["equity"] is not None
                        and row["total_debt"] is not None and row["cash"] is not None):
                    ttm_pretax_total = sum(value for value in ttm_pretax if value is not None)
                    tax_rate = max(0.0, min(0.5, sum(value for value in ttm_tax if value is not None) / ttm_pretax_total)) if ttm_pretax_total > 0 else None
                    if tax_rate is not None:
                        capital_now = row["total_debt"] + row["equity"] - row["cash"]
                        prior = rows[index - 1]
                        if prior["equity"] is not None and prior["total_debt"] is not None and prior["cash"] is not None:
                            capital_before = prior["total_debt"] + prior["equity"] - prior["cash"]
                            average_capital = (capital_now + capital_before) / 2.0
                            if average_capital > 0:
                                row["roic"] = (sum(value for value in ttm_ebit if value is not None) * (1.0 - tax_rate)) / average_capital

        latest = rows[-1] if rows else {}
        metrics = [
            ("current_ratio", "Current ratio", "x", "Higher is generally stronger liquidity; thresholds vary by industry."),
            ("quick_ratio", "Quick ratio", "x", "Cash, short-term investments, and receivables divided by current liabilities."),
            ("debt_equity", "Debt / equity", "x", "Lower is generally less balance-sheet leverage; sector norms vary."),
            ("net_debt_ebitda", "Net debt / EBITDA", "x", "Trailing four-quarter EBITDA; cash-rich companies can have negative net debt."),
            ("interest_coverage", "Interest coverage", "x", "Trailing EBIT divided by trailing interest expense."),
            ("roic", "ROIC", "%", "Trailing after-tax EBIT divided by average invested capital."),
            ("revenue_growth", "Revenue growth YoY", "%", "Quarterly revenue versus the same quarter one year earlier."),
            ("gross_margin", "Gross margin", "%", "Gross profit divided by revenue."),
            ("operating_margin", "Operating margin", "%", "Operating income divided by revenue."),
            ("net_margin", "Net profit margin", "%", "Net income divided by revenue."),
            ("fcf_margin", "Free cash flow margin", "%", "Free cash flow divided by revenue; dollar FCF shown below."),
            ("free_cash_flow", "Free cash flow", "$", "Quarterly free cash flow in dollars."),
            ("fcf_conversion", "FCF / net income", "x", "Trailing four-quarter FCF divided by net income."),
        ]
        for key, _, _, _ in metrics:
            for row in rows:
                row[f"{key}_score"] = _grade_score(key, row.get(key))
        result.update({
            "available": bool(rows), "symbol": symbol, "company": company, "rows": rows,
            "metrics": metrics, "latest": latest, "source": "Yahoo Finance quarterly statements",
        })
        return result
    except Exception as exc:
        result["message"] = f"Could not retrieve the requested company statements ({type(exc).__name__}). Try again later or use a listed-company ticker."
        return result


def _metric_display(metric: str, value: float | None) -> str:
    if value is None:
        return "Not reported"
    if metric.endswith("margin") or metric in {"roic", "revenue_growth"}:
        return f"{value * 100:.1f}%"
    if metric in {"free_cash_flow", "dollars"}:
        return f"${value / 1_000_000:,.0f}M"
    return f"{value:.2f}x"


def _metric_chart(rows: list[dict[str, Any]], metric: str) -> str:
    valid = [(row, safe_float(row.get(metric))) for row in rows]
    valid = [(row, value) for row, value in valid if value is not None]
    if not valid:
        return '<p class="muted health-unavailable">Quarterly history was not returned for this metric.</p>'
    values = [value for _, value in valid]
    low, high = min(values), max(values)
    span = high - low or max(abs(high) * 0.1, 1.0)
    width, height, margin = 440, 90, 14
    points = []
    for i, (row, value) in enumerate(valid):
        x = margin + i * (width - 2 * margin) / max(1, len(valid) - 1)
        y = height - margin - (value - low) / span * (height - 2 * margin)
        points.append((x, y, row))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in points)
    circles = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#477d72"/>' for x, y, _ in points)
    first = points[0][2]["period"].strftime("%b %Y")
    last = points[-1][2]["period"].strftime("%b %Y")
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Quarterly trend from {first} to {last}"><line x1="14" y1="72" x2="426" y2="72" stroke="#e4ded2"/><polyline points="{poly}" fill="none" stroke="#477d72" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>{circles}<text x="14" y="87">{esc(first)}</text><text x="426" y="87" text-anchor="end">{esc(last)}</text></svg>'


def financial_health_html(health: dict[str, Any] | None) -> str:
    title = '<section class="section health-section"><div class="section-head"><h2>Financial Health Check</h2><small>Quarterly reports · color is a screening guide</small></div>'
    if not health:
        return title + '<div class="health-note">To search a company, run <strong>Refresh Morning Brief.bat</strong> and enter a ticker or company name. The page checks reported quarterly fundamentals; true monthly fundamental updates are not available.</div></section>'
    if not health.get("available"):
        return title + f'<div class="health-note">{esc(health.get("message", "No financial data was returned."))}</div></section>'

    latest = health["latest"]
    cards = []
    for metric, label, unit, explanation in health["metrics"]:
        value = latest.get(metric)
        score_metric = "fcf_margin" if metric == "free_cash_flow" else metric
        score = latest.get(f"{score_metric}_score")
        score_text = "No data" if score is None else ("Weak screen" if score < 35 else "Mixed" if score < 65 else "Stronger screen")
        marker = "50%" if score is None else f"{score:.1f}%"
        chart = _metric_chart(health["rows"], metric)
        cards.append(
            f'<article class="health-card"><div class="health-top"><div><h3>{esc(label)}</h3><small>{esc(explanation)}</small></div><div class="health-value">{esc(_metric_display(metric, value))}</div></div>{chart}<div class="health-gradient"><i style="left:{marker}"></i></div><div class="health-scale"><span>Weaker</span><b>{esc(score_text)}</b><span>Stronger</span></div></article>'
        )
    debt = latest.get("total_debt")
    cash = latest.get("cash")
    net_debt = latest.get("net_debt")
    free_cash = latest.get("free_cash_flow")
    meta = (
        f'<div class="health-summary"><div><small>Latest reported quarter</small><strong>{esc(latest["period"].strftime("%b %Y"))}</strong></div>'
        f'<div><small>Total debt</small><strong>{esc(_metric_display("dollars", debt))}</strong></div>'
        f'<div><small>Cash and equivalents</small><strong>{esc(_metric_display("dollars", cash))}</strong></div>'
        f'<div><small>Net debt</small><strong>{esc(_metric_display("dollars", net_debt))}</strong></div>'
        f'<div><small>Free cash flow</small><strong>{esc(_metric_display("free_cash_flow", free_cash))}</strong></div></div>'
    )
    note = '<p class="health-footnote">Company: ' + esc(health["company"]) + ' (' + esc(health["symbol"]) + '). Source: ' + esc(health["source"]) + '. Yahoo statement history can be short or incomplete. Liquidity and leverage norms differ by industry; these heuristic colors are not a credit rating, valuation, or trade signal.</p>'
    positive: list[str] = []
    concerns: list[str] = []
    current_ratio = latest.get("current_ratio")
    quick_ratio = latest.get("quick_ratio")
    debt_equity = latest.get("debt_equity")
    net_debt_ebitda = latest.get("net_debt_ebitda")
    interest_coverage = latest.get("interest_coverage")
    roic = latest.get("roic")
    revenue_growth = latest.get("revenue_growth")
    fcf_conversion = latest.get("fcf_conversion")
    operating_margin = latest.get("operating_margin")
    net_margin = latest.get("net_margin")
    if current_ratio is not None and current_ratio < 1.0 or quick_ratio is not None and quick_ratio < 0.7:
        concerns.append("short-term liquidity is below the broad screening range")
    elif current_ratio is not None and current_ratio >= 1.5 and quick_ratio is not None and quick_ratio >= 1.0:
        positive.append("short-term liquidity screens comfortably")
    if (debt_equity is not None and debt_equity > 2.0) or (net_debt_ebitda is not None and net_debt_ebitda > 5.0) or (interest_coverage is not None and interest_coverage < 2.0):
        concerns.append("leverage or interest coverage deserves closer review")
    elif (interest_coverage is not None and interest_coverage >= 4.0) and (net_debt_ebitda is None or net_debt_ebitda <= 3.0):
        positive.append("debt-service measures look comparatively comfortable")
    if revenue_growth is not None and revenue_growth < 0:
        concerns.append("revenue is below the same quarter a year earlier")
    elif revenue_growth is not None and revenue_growth > 0.05:
        positive.append("revenue is growing year over year")
    if (operating_margin is not None and operating_margin < 0) or (net_margin is not None and net_margin < 0):
        concerns.append("one or more reported profit margins are negative")
    elif roic is not None and roic >= 0.10:
        positive.append("return on invested capital is above the broad 10% reference")
    if free_cash is not None and free_cash < 0:
        concerns.append("latest-quarter free cash flow is negative")
    elif free_cash is not None and free_cash > 0 and (fcf_conversion is None or fcf_conversion >= 0.8):
        positive.append("cash generation is positive relative to reported earnings")
    if len(concerns) >= 3 or (len(concerns) >= 2 and not positive):
        tone = "Cautious fundamentals"
        summary = "This screen points to issues worth resolving before making a new long-term investment case. It does not, by itself, establish a short thesis."
    elif len(positive) >= 3 and len(concerns) <= 1:
        tone = "Supportive fundamentals"
        summary = "Several reported measures support further research. Check valuation, industry peers, and the stock’s price trend before considering an investment."
    else:
        tone = "Mixed or incomplete picture"
        summary = "The available measures do not form a clear fundamental case. Compare with peers and review the latest filing before deciding whether it belongs on your watchlist."
    facts = "".join(f"<li>{esc(item.capitalize())}.</li>" for item in (positive[:3] + concerns[:3]))
    if not facts:
        facts = "<li>There is not enough reported data for a meaningful automated summary.</li>"
    advice = (
        f'<aside class="health-advice"><div class="advice-kicker">Fundamental read · {esc(tone)}</div><p>{esc(summary)}</p>'
        f'<ul>{facts}</ul><p class="health-footnote">This screen cannot determine whether to hold an existing position or time an entry. It does not recommend calls or short sales: options depend on contract price, implied volatility, breakeven, and expiration; long options can expire worthless, and uncovered calls can have unlimited loss. <a href="https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-63" target="_blank" rel="noreferrer">SEC overview of options risks</a>.</p></aside>'
    )
    return title + meta + '<div class="health-grid">' + ''.join(cards) + '</div>' + note + advice + '</section>'


def market_notes(quotes: dict[str, dict[str, Any]], session: dict[str, Any], fred: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    mnq = quotes.get("MNQ=F")
    es = quotes.get("ES=F")
    vix = quotes.get("^VIX")
    qqq = quotes.get("QQQ")
    smh = quotes.get("SMH")
    if mnq and mnq["day_pct"] is not None:
        tone = "higher" if mnq["day_pct"] > 0 else "lower" if mnq["day_pct"] < 0 else "little changed"
        notes.append(f"Latest Yahoo daily bar has MNQ {tone} ({mnq['day_pct']:+.2f}%). This is a daily-bar reference, not a live quote.")
    if mnq and es and mnq["day_pct"] is not None and es["day_pct"] is not None:
        diff = mnq["day_pct"] - es["day_pct"]
        notes.append(f"Nasdaq relative strength vs. ES: {diff:+.2f} percentage points on the latest daily bars.")
    if qqq and smh and qqq["day_pct"] is not None and smh["day_pct"] is not None:
        diff = smh["day_pct"] - qqq["day_pct"]
        notes.append(f"Semiconductors vs. QQQ: SMH {diff:+.2f} percentage points relative today; check whether leadership is broad or narrow.")
    if vix and vix["close"] is not None:
        level = vix["close"]
        regime = "elevated" if level >= 25 else "above its quiet zone" if level >= 20 else "comparatively subdued"
        notes.append(f"VIX closed at {level:.2f}, a {regime} volatility backdrop. Treat this as context, not a direction signal.")
    if session.get("available"):
        bias = "up" if session["rth_change"] > 0 else "down" if session["rth_change"] < 0 else "flat"
        rth_label = local_day_label(datetime.combine(session["rth_date"], dt_time(), LOCAL_TZ))
        notes.append(f"Latest local MNQ cash session ({rth_label}) moved {session['rth_change']:+.2f} points ({bias}); range {session['rth_range']:.2f} points.")
        if session.get("asia_range") is not None:
            notes.append(f"Preceding Asia window (18:00–02:00 CT) covered {session['asia_range']:.2f} points; this is descriptive history, not an Asia-session recommendation.")
        if session.get("median_range") and session["median_range"] > 0:
            ratio = session["rth_range"] / session["median_range"]
            notes.append(f"That cash-session range was {ratio:.2f}× the median of the prior {session.get('prior_range_count', 0)} available sessions in the local file.")
        if session.get("freshness_hours", 0) > 24:
            notes.append(f"The local 5-minute MNQ CSV is about {session['freshness_hours'] / 24:.1f} days old; refresh it before relying on the session read.")
    if fred:
        for item in fred:
            if item["id"] == "CPIAUCSL" and item.get("yoy") is not None:
                notes.append(f"Latest available CPI print: {item['yoy']:.2f}% year-over-year ({item['date']}); release-period date, not the date the market first learned it.")
                break
    if not notes:
        notes.append("Quote data did not refresh. Check the source status below and try again later.")
    return notes[:6]


def finbert_scores(headlines: list[dict[str, Any]]) -> tuple[dict[str, tuple[str, float]], str | None]:
    if os.getenv("MORNING_FINBERT", "0") != "1":
        return {}, "FinBERT is off by default to keep the morning refresh fast; set MORNING_FINBERT=1 after installing and caching the model."
    try:
        import torch  # type: ignore
        from transformers import pipeline  # type: ignore
        model = os.getenv("FINBERT_MODEL_PATH", "ProsusAI/finbert")
        if (ROOT / model).is_dir():  # relative paths in .env resolve from this folder, not the shell's
            model = str(ROOT / model)
        device = 0 if torch.cuda.is_available() else -1
        classifier = pipeline("text-classification", model=model, tokenizer=model, local_files_only=True, device=device)
        scored: dict[str, tuple[str, float]] = {}
        for item in headlines:
            if item.get("title"):
                answer = classifier(item["title"][:512], truncation=True)[0]
                scored[item["title"].casefold()] = (str(answer.get("label", "neutral")).title(), float(answer.get("score", 0.0)))
        return scored, None
    except Exception as exc:
        return {}, f"FinBERT was requested but its package/model is unavailable ({type(exc).__name__})."


def render_report(
    quotes: dict[str, dict[str, Any]], headlines: list[dict[str, Any]], events: list[CalendarEvent],
    event_notes: list[str], filings: list[dict[str, Any]], sec_note: str | None,
    fred: list[dict[str, Any]], fred_note: str | None, session: dict[str, Any],
    scores: dict[str, tuple[str, float]], score_note: str | None,
    report_path: Path, health: dict[str, Any] | None = None,
) -> None:
    generated = datetime.now(LOCAL_TZ)
    source_dates = [row["date"] for row in quotes.values() if row.get("date")]
    latest_quote_date = max(source_dates) if source_dates else None
    quote_date = f"{latest_quote_date.strftime('%a %b')} {latest_quote_date.day}" if latest_quote_date else "unavailable"
    notes = market_notes(quotes, session, fred)
    by_group: dict[str, list[dict[str, Any]]] = {}
    for symbol, name, group in WATCHLIST:
        row = quotes.get(symbol, {"symbol": symbol, "name": name, "group": group})
        by_group.setdefault(group, []).append(row)

    watch_html = []
    for group, rows in by_group.items():
        table_rows = []
        for row in rows:
            day = row.get("day_pct")
            week = row.get("period_pct")
            color_day = "up" if day is not None and day > 0 else "down" if day is not None and day < 0 else "muted"
            table_rows.append(
                f"<tr><td><b>{esc(row.get('symbol'))}</b><small>{esc(row.get('name'))}</small></td>"
                f"<td class='num'>{fmt_price(row.get('close'))}</td><td class='num {color_day}'>{fmt_pct(day)}</td>"
                f"<td class='num'>{fmt_pct(week)}</td></tr>"
            )
        watch_html.append(f"<section class='watch-group'><h3>{esc(group)}</h3><table><thead><tr><th>Instrument</th><th>Last daily close</th><th>1-day</th><th>~5-day</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></section>")

    news_html = []
    for item in headlines:
        if item.get("error"):
            news_html.append(f"<p class='muted'>{esc(item['error'])}</p>")
            continue
        published = item.get("published")
        local_pub = published.astimezone(LOCAL_TZ) if published else None
        stamp = f"{local_day_label(local_pub)} · {local_clock_label(local_pub)} CT" if local_pub else "time unavailable"
        tone_html = ""
        score = scores.get(item["title"].casefold())
        if score:
            tone_html = f"<span class='sentiment {esc(score[0].lower())}'>{esc(score[0])} {score[1]:.0%}</span>"
        news_html.append(f"<article class='headline'><a href='{esc(item['url'])}' target='_blank' rel='noreferrer'>{esc(item['title'])}</a><div class='meta'>{esc(item.get('source','Yahoo Finance'))} · {esc(stamp)} {tone_html}</div></article>")

    cal_html = []
    for event in events:
        when = event.when.astimezone(LOCAL_TZ)
        date_label = local_day_label(when)
        time_label = f"{local_clock_label(when)} CT" if when.hour or when.minute else "All day / time not listed"
        cal_html.append(f"<tr><td>{esc(date_label)}<small>{esc(time_label)}</small></td><td><b>{esc(event.title)}</b><small>{esc(event.source)}</small></td><td><a href='{esc(event.url)}' target='_blank' rel='noreferrer'>Official source ↗</a></td></tr>")
    if not cal_html:
        cal_html.append("<tr><td colspan='3' class='muted'>No selected high-impact releases or watchlist earnings found in the next 14 days.</td></tr>")

    filing_html = []
    for item in filings:
        filing_html.append(f"<tr><td>{esc(item['date'].strftime('%b'))} {item['date'].day}</td><td><b>{esc(item['ticker'])}</b></td><td>{esc(item['form'])}</td><td><a href='{esc(item['url'])}' target='_blank' rel='noreferrer'>Read filing ↗</a></td></tr>")
    if not filing_html:
        filing_html.append(f"<tr><td colspan='4' class='muted'>{esc(sec_note or 'No recent 10-K, 10-Q, or 8-K filings found for the tracked companies.')}</td></tr>")

    macro_html = []
    for item in fred:
        if item["id"] == "CPIAUCSL" and item.get("yoy") is not None:
            value = f"{item['value']:,.1f} · {item['yoy']:+.2f}% YoY"
        else:
            suffix = "%" if item["unit"] == "percent" else ""
            value = f"{item['value']:.2f}{suffix}"
        macro_html.append(f"<div class='macro-chip'><span>{esc(item['label'])}</span><b>{esc(value)}</b><small>Reference period {esc(item['date'])}</small></div>")
    if not macro_html:
        macro_html.append(f"<div class='setup-note'>{esc(fred_note or 'No FRED series available.')}</div>")

    context_html = "".join(f"<li>{esc(note)}</li>" for note in notes)
    calendar_notes_html = "".join(f"<p class='source-note'>{esc(note)}</p>" for note in event_notes)
    score_note_html = f"<p class='source-note'>{esc(score_note)}</p>" if score_note else ""
    csv_age = ""
    if session.get("available"):
        last_seen = session["latest_stamp"].strftime("%Y-%m-%d") + " " + local_clock_label(session["latest_stamp"]) + " CT"
        freshness = "CURRENT" if session.get("freshness_hours", 0) <= 24 else "STALE"
        csv_age = f"<span class='pill'>{freshness} · MNQ file through {esc(last_seen)}</span>"

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Morning Market Brief · {generated:%Y-%m-%d}</title>
<style>
.health-section{{padding-top:18px;border-top:2px solid var(--line)}}.health-note{{background:#fffdf8;border:1px solid var(--line);border-radius:12px;padding:18px;color:var(--muted)}}.health-summary{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:9px;margin:12px 0}}.health-summary>div{{background:#fffdf8;border:1px solid var(--line);border-radius:9px;padding:11px}}.health-summary small,.health-summary strong{{display:block}}.health-summary strong{{margin-top:4px;font-size:17px;font-variant-numeric:tabular-nums}}.health-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}}.health-card{{background:#fffdf8;border:1px solid var(--line);border-radius:11px;padding:11px}}.health-top{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}.health-top h3{{margin:0 0 3px;color:var(--ink);font-size:14px}}.health-top small{{max-width:330px;color:var(--muted);line-height:1.35}}.health-value{{font-size:18px;font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}}.health-card svg{{display:block;width:100%;height:auto;margin:9px 0 3px;overflow:visible}}.health-card svg text{{fill:var(--muted);font-size:10px}}.health-gradient{{height:8px;border-radius:99px;background:linear-gradient(90deg,#b95349 0%,#d5a34c 50%,#4c9275 100%);position:relative;margin:7px 3px}}.health-gradient i{{position:absolute;top:-3px;width:3px;height:14px;background:#17212b;border:1px solid #fff;border-radius:3px;transform:translateX(-50%)}}.health-scale{{display:flex;justify-content:space-between;align-items:center;font-size:10px;color:var(--muted)}}.health-scale b{{font-size:11px;color:var(--ink)}}.health-footnote{{font-size:11px;color:var(--muted);line-height:1.5;margin:13px 2px 0}}.health-advice{{margin-top:14px;padding:16px 18px;background:#fffdf8;border:1px solid var(--line);border-left:4px solid var(--blue);border-radius:10px}}.advice-kicker{{font-size:12px;font-weight:800;letter-spacing:.7px;text-transform:uppercase;color:var(--blue)}}.health-advice p{{margin:7px 0}}.health-advice ul{{margin:8px 0;padding-left:20px}}.health-advice li{{padding:2px 0}}.health-unavailable{{min-height:95px;padding:20px 0}}@media(max-width:960px){{.health-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:620px){{.health-grid{{grid-template-columns:1fr}}.health-summary{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
:root{{--ink:#17212b;--muted:#687784;--paper:#f4f1e9;--card:#fffdf8;--line:#dfdbd1;--blue:#315e76;--green:#28775d;--red:#a5493f;--gold:#ad782e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 Segoe UI,Arial,sans-serif}}.page{{max-width:1250px;margin:0 auto;padding:32px 24px 60px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;border-bottom:2px solid var(--ink);padding-bottom:18px;margin-bottom:22px}}h1{{font:700 clamp(30px,5vw,46px)/1.05 Georgia,serif;margin:0;letter-spacing:-.5px}}.kicker{{color:var(--blue);font-size:12px;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:9px}}.stamp{{color:var(--muted);text-align:right;font-size:13px}}.pill{{display:inline-block;background:#e9e5db;color:#53616a;border-radius:999px;padding:5px 10px;font-size:11px;margin-top:7px}}
.intro{{display:grid;grid-template-columns:1.5fr 1fr;gap:16px;margin-bottom:18px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:19px 21px;box-shadow:0 3px 14px #29271d0a}}.card h2{{font:700 21px Georgia,serif;margin:0 0 12px}}.context ul{{margin:0;padding-left:20px}}.context li{{padding:4px 0}}.macro{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}.macro-chip{{border:1px solid var(--line);border-radius:9px;padding:10px}}.macro-chip span,.macro-chip small{{display:block;color:var(--muted);font-size:11px}}.macro-chip b{{font-size:18px}}.setup-note,.source-note{{font-size:12px;color:var(--muted)}}
.section{{margin-top:18px}}.section-head{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin:0 1px 9px}}.section h2{{font:700 24px Georgia,serif;margin:0}}.section-head small{{color:var(--muted)}}.watchgrid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.watch-group{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:13px 15px}}.watch-group h3{{margin:0 0 8px;color:var(--blue);font-size:13px;text-transform:uppercase;letter-spacing:1px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th{{text-align:left;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.6px;font-weight:700;padding:7px 5px;border-bottom:1px solid var(--line)}}td{{padding:7px 5px;border-bottom:1px solid #eeebe4;vertical-align:top}}tbody tr:last-child td{{border-bottom:0}}small{{display:block;color:var(--muted);font-size:11px}}.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}.up{{color:var(--green);font-weight:700}}.down{{color:var(--red);font-weight:700}}.muted{{color:var(--muted)}}.headline{{padding:11px 0;border-bottom:1px solid var(--line)}}.headline:last-child{{border-bottom:0}}a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}.meta{{color:var(--muted);font-size:11px;margin-top:4px}}.sentiment{{display:inline-block;border-radius:4px;padding:1px 5px;margin-left:6px;font-size:10px}}.positive{{background:#e2f1e8;color:var(--green)}}.negative{{background:#f6e3df;color:var(--red)}}.neutral{{background:#ece9e2;color:#69737b}}.calendar td:first-child{{white-space:nowrap;width:110px}}.calendar td:last-child{{text-align:right;white-space:nowrap}}.two-col{{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}}.source-foot{{margin-top:24px;padding-top:13px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}}.tag{{display:inline-block;font-size:10px;background:#eee9df;padding:2px 6px;border-radius:4px;color:#5c6470}}button{{border:0;background:var(--blue);color:white;padding:9px 13px;border-radius:7px;font-weight:600;cursor:pointer}}@media(max-width:760px){{.page{{padding:20px 13px}}header{{align-items:flex-start;flex-direction:column}}.stamp{{text-align:left}}.intro,.two-col,.watchgrid{{grid-template-columns:1fr}}.num{{font-size:12px}}}}
</style></head><body><main class="page">
<header><div><div class="kicker">Personal market newsletter · Nasdaq focus</div><h1>Morning Market Brief</h1></div><div class="stamp">Prepared {generated.strftime('%A, %B')} {generated.day}, {generated.year}<br>{local_clock_label(generated)} Central · quote bars through {esc(quote_date)}<br>{csv_age}</div></header>
<div class="intro"><section class="card context"><h2>What to have in mind</h2><ul>{context_html}</ul></section><section class="card"><h2>Macro dashboard</h2><div class="macro">{''.join(macro_html)}</div></section></div>
<section class="section"><div class="section-head"><h2>Watchlist</h2><small>Daily bars from Yahoo Finance · latest returned close, not a live quote</small></div><div class="watchgrid">{''.join(watch_html)}</div></section>
<div class="two-col section"><section class="card"><div class="section-head"><h2>News worth scanning</h2><span class="tag">Yahoo Finance</span></div>{''.join(news_html) if news_html else '<p class="muted">No headlines returned.</p>'}{score_note_html}</section>
<section class="card"><div class="section-head"><h2>Coming up · next 14 days</h2><small>Times shown in Central</small></div><table class="calendar"><thead><tr><th>When</th><th>Event</th><th>Source</th></tr></thead><tbody>{''.join(cal_html)}</tbody></table>{calendar_notes_html}</section></div>
<div class="two-col section"><section class="card"><div class="section-head"><h2>Company filings</h2><span class="tag">SEC EDGAR</span></div><table><thead><tr><th>Filed</th><th>Company</th><th>Form</th><th>Document</th></tr></thead><tbody>{''.join(filing_html)}</tbody></table>{f'<p class="source-note">{esc(sec_note)}</p>' if sec_note else ''}</section>
<section class="card"><h2>Read this as context</h2><p>These observations describe the latest data returned by each source. They are not trade signals or a forecast. Check the timestamp, compare Nasdaq with the broader indexes, and let your tested setup determine whether you participate.</p><p>Yahoo intraday history is limited and can be revised or incomplete. The local MNQ CSV powers the session-range notes; refresh it regularly. Macro reference periods are not the same as release timestamps.</p><p><button onclick="window.print()">Print / save as PDF</button></p></section></div>
{financial_health_html(health)}
<footer class="source-foot">Sources: Yahoo Finance quotes and headlines; local MNQ 5-minute CSV; BLS and BEA official release calendars; Federal Reserve FOMC calendar; SEC EDGAR filings; FRED macro series when configured. Generated locally at {generated:%Y-%m-%d %H:%M:%S %Z}. This newsletter is for personal research.</footer>
</main></body></html>"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(document, encoding="utf-8")


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description="Generate your local morning market newsletter.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help=f"MNQ 5-minute CSV (default: {DEFAULT_CSV})")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT, help=f"HTML output file (default: {DEFAULT_REPORT})")
    parser.add_argument("--financial-query", help="Search Yahoo Finance for a company/ticker and add its quarterly health check")
    parser.add_argument("--no-open", action="store_true", help="Write the report without opening it in your browser.")
    args = parser.parse_args()

    financial_query = args.financial_query
    if financial_query is None and sys.stdin.isatty():
        try:
            financial_query = input("Ticker or company name for the Financial Health Check (press Enter to skip): ").strip() or None
        except EOFError:
            financial_query = None

    print("Refreshing Yahoo daily quotes and headlines…")
    quotes = latest_daily_quotes()
    headlines = yahoo_news()
    print("Checking official release calendars and watchlist earnings…")
    events, event_notes = event_calendar()
    print("Checking SEC filings and macro series…")
    filings, sec_note = sec_filings()
    fred, fred_note = fred_context()
    session = mnq_session_context(args.csv)
    scores, score_note = finbert_scores(headlines)
    health = None
    if financial_query:
        print(f"Searching quarterly company fundamentals for {financial_query}…")
        health = financial_health_data(financial_query)
    render_report(quotes, headlines, events, event_notes, filings, sec_note, fred, fred_note, session, scores, score_note, args.out, health)
    print(f"Morning brief saved: {args.out.resolve()}")
    if not args.no_open:
        try:
            report_path = args.out.resolve()
            if sys.platform == "win32":
                os.startfile(str(report_path))
            else:
                webbrowser.open_new_tab(report_path.as_uri())
            print("Opened the morning brief in your default browser.")
        except Exception as exc:
            print(f"Could not open the browser automatically ({type(exc).__name__}). Open this file manually: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

