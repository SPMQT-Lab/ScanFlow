"""Probe which COM method + key actually returns a temperature on this rig.

Run ON THE LAB PC with STMAFM open and connected:

    python tools/diagnose_temperature.py

It connects, does the py-createc refresh poke, then for every candidate
sensor key tries BOTH read methods (getparam, getp) and prints which one
returns a value. Paste the output back so the key list can be locked in
if the auto-discovery in TemperatureMonitor still misses your rig.
"""

from __future__ import annotations

from scanflow.core import STMClient

CANDIDATE_KEYS = [
    "T-STM:", "T-STM", "T_STM",
    "OneK_4K_Cryo", "OneK_1K_Cryo", "OneK_STM",
    "T_ADC2[K]", "T_ADC2", "T_ADC3[K]", "T_ADC3",
    "T_AUXADC6[K]", "T_AUXADC6", "T_AUXADC7[K]", "T_AUXADC7",
]


def _poke(stm: STMClient) -> None:
    raw = stm.raw
    for how, fn in (("raw.setparam", lambda: raw.setparam("MEMO_STMAFM", "")),
                    ("client.setp", lambda: stm.setp("MEMO_STMAFM", ""))):
        try:
            fn()
            print(f"refresh poke via {how}: OK")
        except Exception as e:
            print(f"refresh poke via {how}: FAILED ({e})")


def main() -> int:
    stm = STMClient()
    if not stm.connect():
        print("Could not connect to STMAFM — is it running and connected?")
        return 1
    print("Connected. Probing temperature keys…\n")
    _poke(stm)
    raw = stm.raw
    print(f"\n{'key':<16} {'getparam':<22} {'getp':<22}")
    print("-" * 60)
    for key in CANDIDATE_KEYS:
        try:
            gp = raw.getparam(key)
        except Exception as e:
            gp = f"ERR:{type(e).__name__}"
        try:
            gpp = stm.getp(key, "")
        except Exception as e:
            gpp = f"ERR:{type(e).__name__}"
        print(f"{key:<16} {str(gp)[:20]:<22} {str(gpp)[:20]:<22}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
