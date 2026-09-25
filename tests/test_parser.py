import glob
import os

import numpy as np
import pytest
from conftest import EXAMPLES_DIR, NT_SAMPLE

from nt8analyzer.parser import ParseError, parse_file, parse_number, parse_text


@pytest.mark.parametrize(
    "text, hint, expected",
    [
        ("$100.00", ".", 100.0),
        ("($2.00)", ".", -2.0),
        ("$1,234.50", ".", 1234.5),
        ("-$5.25", ".", -5.25),
        ("20168.25", ".", 20168.25),
        ("1,234", ".", 1234.0),
        ("1.234,56 €", ",", 1234.56),
        ("(2,00 €)", ",", -2.0),
        ("20168,25", ",", 20168.25),
        ("-12,5", ",", -12.5),
        ("", ".", 0.0),
        ("$0.00", ".", 0.0),
    ],
)
def test_parse_number(text, hint, expected):
    assert parse_number(text, hint) == pytest.approx(expected)


def test_parse_ninjatrader_export():
    parsed = parse_text(NT_SAMPLE, "VaultbreakNQ.csv")
    ts = parsed.trades
    assert len(ts) == 4
    assert parsed.strategy_name == "VaultbreakNQ"
    assert parsed.instrument == "MNQ 12-26"
    assert parsed.warnings == []
    np.testing.assert_allclose(ts.profit, [-2.0, 100.0, 100.0, -200.0])
    assert ts.entry_time[0] == np.datetime64("2024-02-01T14:00:00")
    assert ts.exit_time[1] == np.datetime64("2024-02-02T10:50:00")
    assert ts.exit_name[3] == "Stop loss"
    np.testing.assert_allclose(ts.mae, [6.5, 95.5, 21.0, 200.0])
    np.testing.assert_allclose(ts.bars, [12, 13, 9, 24])


def test_parse_sorts_rows_chronologically():
    lines = NT_SAMPLE.strip().splitlines()
    shuffled = "\n".join([lines[0], lines[3], lines[1], lines[4], lines[2]])
    ts = parse_text(shuffled).trades
    assert np.all(np.diff(ts.exit_time.astype(np.int64)) >= 0)
    np.testing.assert_allclose(ts.trade_number, [1, 2, 3, 4])


def test_parse_european_format():
    text = (
        "Trade number;Instrument;Account;Strategy;Market pos.;Qty;Entry price;Exit price;Entry time;Exit time;"
        "Entry name;Exit name;Profit;Cum. net profit;Commission\n"
        "1;ES 12-26;Sim;Test;Short;1;4700,25;4690,25;13/03/2024 15:30:00;13/03/2024 16:00:00;E;X;500,00 €;500,00 €;0,00 €\n"
        "2;ES 12-26;Sim;Test;Long;1;4690,00;4680,00;14/03/2024 09:30:00;14/03/2024 10:00:00;E;X;-1.250,00 €;-750,00 €;0,00 €\n"
    )
    parsed = parse_text(text)
    np.testing.assert_allclose(parsed.trades.profit, [500.0, -1250.0])
    assert parsed.trades.entry_time[0] == np.datetime64("2024-03-13T15:30:00")
    assert parsed.warnings == []


def test_parse_rejects_garbage():
    with pytest.raises(ParseError):
        parse_text("colonna1,colonna2\n1,2\n")
    with pytest.raises(ParseError):
        parse_text("")


def test_cumulative_mismatch_is_reported():
    broken = NT_SAMPLE.replace("($200.00),($2.00)", "($200.00),($9.00)")
    parsed = parse_text(broken)
    assert any("Cum. net profit" in w for w in parsed.warnings)


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.csv"))))
def test_example_files(path):
    parsed = parse_file(path)
    assert len(parsed.trades) > 100
    assert parsed.warnings == []
