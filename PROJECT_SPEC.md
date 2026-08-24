# Project Specification

## Pipeline
Full TASI Universe -> fast screening -> deep analysis -> scoring -> risk -> probability -> Signal Bot -> Trade ID -> Profit/Loss tracking -> weekly Report.

## Signal fields
Symbol, Arabic/English name, direction, entry zone, SL, TP1/TP2/TP3, R/R, score, probability, probability status/samples, strategy, market regime, sector, discovery time, expected TP windows.

## Probability
ليست AI prediction. تعتمد على نتائج تاريخية مشابهة مخزنة محلياً مع minimum sample. إذا لم تتوفر عينة كافية تكون UNVALIDATED ولا تتحول إلى إشارة.

## Paper Trading
لا توجد broker/execution APIs.

## Data provider
SAHMK isolated behind `DataProvider`; future providers can be added without changing strategy.
