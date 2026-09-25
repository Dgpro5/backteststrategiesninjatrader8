import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import tempfile

    from PySide6.QtCore import QSettings

    # I test non devono toccare le impostazioni reali dell'utente (capitale, opzioni Monte Carlo).
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="nt8analyzer-tests-"))
except ImportError:
    pass

EXAMPLES_DIR = os.path.join(ROOT, "examples")

NT_SAMPLE = """Trade number,Instrument,Account,Strategy,Market pos.,Qty,Entry price,Exit price,Entry time,Exit time,Entry name,Exit name,Profit,Cum. net profit,Commission,Clearing Fee,Exchange Fee,IP Fee,NFA Fee,MAE,MFE,ETD,Bars,
1,MNQ 12-26,Backtest,VaultbreakNQ,Long,1,20168.25,20167.25,2/1/2024 2:00:00 PM,2/1/2024 2:55:00 PM,VB_Long,VB_Time,($2.00),($2.00),$0.00,$0.00,$0.00,$0.00,$0.00,$6.50,$64.00,$66.00,12,
2,MNQ 12-26,Backtest,VaultbreakNQ,Long,1,20400.50,20450.50,2/2/2024 9:50:00 AM,2/2/2024 10:50:00 AM,VB_Long,Profit target,$100.00,$98.00,$0.00,$0.00,$0.00,$0.00,$0.00,$95.50,$100.00,$0.00,13,
3,MNQ 12-26,Backtest,VaultbreakNQ,Long,1,20449.25,20499.25,2/2/2024 10:55:00 AM,2/2/2024 11:35:00 AM,VB_Long,Profit target,$100.00,$198.00,$0.00,$0.00,$0.00,$0.00,$0.00,$21.00,$100.00,$0.00,9,
4,MNQ 12-26,Backtest,VaultbreakNQ,Long,1,20931.75,20831.75,3/12/2024 9:55:00 AM,3/12/2024 11:50:00 AM,VB_Long,Stop loss,($200.00),($2.00),$0.00,$0.00,$0.00,$0.00,$0.00,$200.00,$49.00,$249.00,24,
"""
