"""Read-only real-data benchmark of the production scheduler and signal equivalence."""
from datetime import date
import json,time
from pathlib import Path
from quant.multi_jobs import stock_results
from quant.multiscreener import REGISTRY


def main():
    cutoff=date(2026,10,14)
    symbols=['300138.SZ','600036.SH','601318.SH','600519.SH','000001.SZ','000858.SZ','300750.SZ','002415.SZ']
    work=[(i,{'symbol':s,'name':s},cutoff,[c.__name__ for c,_ in REGISTRY]) for i,s in enumerate(symbols,1)]
    baseline=None;report=[]
    for workers in (1,4):
        started=time.perf_counter()
        rows=sorted(stock_results(work,workers,lambda:False,lambda _:None),key=lambda r:r[0])
        elapsed=time.perf_counter()-started
        if baseline is None:baseline=rows
        assert rows==baseline,'Parallel execution changed output'
        result={'workers':workers,'elapsed_seconds':round(elapsed,3),'stocks':len(rows),
                'signals':sum(len(r[2]['signals']) for r in rows),'errors':sum(len(r[2]['errors']) for r in rows)}
        report.append(result);print(json.dumps(result),flush=True)
    Path('.runtime/parallel-scheduler-benchmark.json').write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':main()
