# Morning Briefing

A Python script that builds a one-page morning market brief for a Nasdaq-futures trader and opens it in the browser. It collects free public data into a single HTML file. It does not place trades or make recommendations.

## What's in the brief

- **Watchlist**: index futures (MNQ, ES, RTY, YM), gold, crude, index ETFs, VIX, the 10-year yield, the dollar index and the largest Nasdaq stocks, from Yahoo Finance daily bars.
- **Headlines**: from Yahoo Finance RSS, optionally tagged positive, negative or neutral by [FinBERT](https://huggingface.co/ProsusAI/finbert), a language model trained on financial text.
- **Calendar for the next 14 days**: BLS and BEA data releases (CPI, jobs, GDP and others), FOMC meetings, and earnings dates for the tracked companies.
- **SEC filings**: recent 10-K, 10-Q and 8-K filings for the large Nasdaq names, from SEC EDGAR.
- **Macro series** (optional): CPI, fed funds rate, unemployment and the 10-year Treasury yield, from FRED.
- **MNQ session context**: prior-session range and VWAP from a local 5-minute CSV, with a warning when the file is stale.
- **Company health check** (optional): enter a ticker to add up to twelve quarters of liquidity, leverage, margin and cash-flow charts.

Every item links back to its source and shows its timestamp.

## Setup

Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Then fill in `.env` (see [Keys](#keys)).

## Run

Double-click `Refresh Morning Brief.bat`, or:

```powershell
.\.venv\Scripts\python.exe morning_terminal.py                 # build and open morning_brief.html
.\.venv\Scripts\python.exe morning_terminal.py --no-open       # build only
.\.venv\Scripts\python.exe morning_terminal.py --financial-query NVDA   # add a company health check
.\.venv\Scripts\python.exe morning_terminal.py --csv path\to\mnq_5m.csv --out brief.html
```

The MNQ session panel reads `data/mnq_yahoo_5m.csv` by default (columns `timestamp,open,high,low,close,volume`). If the file is missing, the panel is skipped and the rest of the brief still builds.

To change what's tracked, edit `WATCHLIST`, `YAHOO_NEWS_SYMBOLS` and `EARNINGS_SYMBOLS` near the top of `morning_terminal.py`.

## Keys

All settings live in `.env`, which is git-ignored. [`.env.example`](.env.example) lists them with blank values. The script works with all of them blank: each optional panel is skipped and a note on the page says why.

| Variable | Needed for | How to get it |
|---|---|---|
| `SEC_USER_AGENT` | SEC filings panel | Not a key. The SEC asks automated clients to identify themselves, e.g. `MorningBrief/1.0 (Your Name you@example.com)`. |
| `FRED_API_KEY` | Macro series panel | Free at [fredaccount.stlouisfed.org](https://fredaccount.stlouisfed.org/). |
| `MORNING_FINBERT` | Headline sentiment | Set to `1` to turn it on. |
| `FINBERT_MODEL_PATH` | Headline sentiment | Folder with the downloaded model, e.g. `Models/finbert` (relative to this folder). |

## FinBERT model (optional)

The model weights (~420 MB) are not in the repository. To use headline sentiment, install PyTorch and Transformers, then download [ProsusAI/finbert](https://huggingface.co/ProsusAI/finbert) from Hugging Face:

```powershell
.\.venv\Scripts\python.exe -m pip install torch transformers huggingface_hub
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('ProsusAI/finbert', local_dir='Models/finbert')"
```

Then set `MORNING_FINBERT=1` and `FINBERT_MODEL_PATH=Models/finbert` in `.env`. The script loads the model from disk only (no download at run time) and uses an NVIDIA GPU if PyTorch can see one. Sentiment labels describe a headline's wording; they aren't price forecasts.

## Data limitations

- Yahoo Finance data via `yfinance` is unofficial, delayed and meant for personal use.
- Official calendars can change, so each event links to the publisher to confirm.
- The brief is context for a human reader, not a trading signal.
