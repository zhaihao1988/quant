"""Compare actual stored run against original strategy sources with identical inputs."""
import json,sys
from pathlib import Path
from datetime import date
from dataclasses import asdict

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_multiscreener import original_classes
from quant.multiscreener import load_strategy_data,json_safe
from quant.legacy_strategies.base_strategy import StrategyContext

run=json.loads((ROOT/'.runtime/multi-browser-result.json').read_text(encoding='utf-8'))
cutoff=date.fromisoformat(run['effective_date'])
all_original=[]
for stock in run['result']['coverage']:
    symbol=stock['symbol'];data,_=load_strategy_data(symbol,cutoff)
    for cls,_ in original_classes():
        raw=cls(StrategyContext(None,cutoff,{})).run_for_stock(symbol,cutoff,{k:v.copy(deep=True) for k,v in data.items()})
        all_original.extend(json_safe(asdict(sig)) for sig in (raw or []) if sig.signal_date==cutoff and sig.signal_type!='NO_SIGNAL')
fields=['symbol','signal_date','strategy_name','signal_type','signal_score','details','notes','timeframe']
actual=[{f:row[f] for f in fields} for row in run['result']['signals']]
assert actual==all_original
print(json.dumps({'symbols':len(run['result']['coverage']),'strategies_per_symbol':7,'identical_signals':len(actual)},ensure_ascii=False))
