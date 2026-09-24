"""Genera file CSV di esempio (dati sintetici) nel formato export di NinjaTrader 8.

Uso: python tools/genera_esempi.py [cartella_output]
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta

HEADER = (
    "Trade number,Instrument,Account,Strategy,Market pos.,Qty,Entry price,Exit price,Entry time,Exit time,"
    "Entry name,Exit name,Profit,Cum. net profit,Commission,Clearing Fee,Exchange Fee,IP Fee,NFA Fee,"
    "MAE,MFE,ETD,Bars,"
)

EXAMPLES = [
    # nome, strumento, $ per punto, tick, prob. trade/giorno, target $, stop $, prob. vincita, seed, orari
    ("EsempioBreakoutNQ", "MNQ 12-26", 2.0, 0.25, 0.55, 100.0, 200.0, 0.66, 11, (9, 35, 13, 30)),
    ("EsempioMeanRevES", "MES 12-26", 5.0, 0.25, 0.45, 75.0, 60.0, 0.47, 23, (10, 0, 15, 0)),
    ("EsempioTrendCL", "MCL 12-26", 100.0, 0.01, 0.30, 150.0, 80.0, 0.40, 37, (9, 0, 14, 0)),
]


def money(value: float) -> str:
    return f"(${abs(value):.2f})" if value < 0 else f"${value:.2f}"


def nt_time(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.month}/{dt.day}/{dt.year} {hour}:{dt.minute:02d}:{dt.second:02d} {ampm}"


def generate(name, instrument, point_value, tick, p_trade, target, stop, p_win, seed, hours) -> list[str]:
    rng = random.Random(seed)
    day = datetime(2024, 1, 2)
    end = datetime(2026, 9, 18)
    price = {"MNQ 12-26": 17000.0, "MES 12-26": 4700.0, "MCL 12-26": 72.0}[instrument]
    h0, m0, h1, m1 = hours
    rows, cum, number = [], 0.0, 0
    while day <= end:
        if day.weekday() < 5 and rng.random() < p_trade:
            start = day.replace(hour=h0, minute=m0)
            last = day.replace(hour=h1, minute=m1)
            slots = int((last - start).total_seconds() // 300)
            entry = start + timedelta(minutes=5 * rng.randint(0, slots))
            bars = rng.randint(2, 40)
            exit_ = entry + timedelta(minutes=5 * bars)
            long = rng.random() < 0.6
            r = rng.random()
            if r < p_win:
                profit, exit_name = target, "Profit target"
            elif r < p_win + (1 - p_win) * 0.6:
                profit, exit_name = -stop, "Stop loss"
            else:
                profit, exit_name = round(rng.uniform(-stop * 0.8, target * 0.8) / 0.5) * 0.5, "Exit on session close"
            points = profit / point_value
            entry_price = round(price / tick) * tick
            exit_price = entry_price + (points if long else -points)
            mae = abs(min(profit, 0.0)) + round(rng.uniform(0, stop * 0.4), 2)
            mfe = max(profit, 0.0) + round(rng.uniform(0, target * 0.4), 2)
            etd = mfe - profit
            cum += profit
            number += 1
            rows.append(
                ",".join(
                    [
                        str(number),
                        instrument,
                        "Backtest",
                        name,
                        "Long" if long else "Short",
                        "1",
                        f"{entry_price:.2f}",
                        f"{exit_price:.2f}",
                        nt_time(entry),
                        nt_time(exit_),
                        "Entry_Long" if long else "Entry_Short",
                        exit_name,
                        money(profit),
                        money(cum),
                        "$0.00",
                        "$0.00",
                        "$0.00",
                        "$0.00",
                        "$0.00",
                        money(mae),
                        money(mfe),
                        money(etd),
                        str(bars),
                    ]
                )
                + ","
            )
            price = max(tick * 100, price * (1 + rng.gauss(0.0003, 0.012)))
        day += timedelta(days=1)
    return rows


def main(folder: str) -> None:
    os.makedirs(folder, exist_ok=True)
    for spec in EXAMPLES:
        rows = generate(*spec)
        path = os.path.join(folder, f"{spec[0]}.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write(HEADER + "\n" + "\n".join(rows) + "\n")
        print(f"{path}: {len(rows)} trade")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "examples"))
