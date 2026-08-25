# Project Specification

## Pipeline — SAHMK Free mode

Manual `/signal` -> market-hours guard -> TASI active-stock ranking (`/market/volume/`) -> screen up to 50 -> detailed single quotes for up to 5 finalists -> quote-only momentum/risk checks -> Signal Bot -> Paper Trade -> Profit/Loss tracking -> weekly report.

No automatic signal discovery. Scheduler only monitors open paper trades and scheduled messages while the service is running.

## Signal fields

Symbol, Arabic/English name, direction, entry zone, SL, TP1/TP2/TP3, R/R, screening score, empirical probability status/samples, strategy, market regime, sector, discovery time, expected TP windows.

## Probability

Probability is never invented. Before enough closed paper trades exist in the same bucket, the signal is marked `UNVALIDATED` and shows the real sample count. This does not block initial Paper Trades, because those trades are needed to build the empirical sample. After 30 outcomes in the bucket, probability becomes `VALIDATED` and `MIN_PROBABILITY` is enforced.

## Data provider

SAHMK is isolated behind `DataProvider`.

Free mode deliberately avoids:
- `/quotes/` bulk endpoint (Starter+)
- `/historical/{symbol}/` (Starter+)

Free mode uses:
- `/market/volume/`
- `/quote/{symbol}/`
- `/market/summary/`
- `/companies/`

## Paper Trading

No broker/execution APIs are present.
