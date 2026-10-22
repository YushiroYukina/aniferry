"""Display-only episode mapping, source summaries and shared search pacing."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import threading
import time
import urllib.parse


@dataclass(frozen=True)
class EpisodeView:
    enabled: bool = False
    offset: int = 0
    numbering: str = 'absolute'
    range_basis: str = 'local'

    @classmethod
    def from_options(cls, options, eligible=True):
        return cls(bool(options.get('dual') and eligible), int(options.get('offset',0)),
                   options.get('numbering','absolute'), options.get('range_basis','local'))

    def pair(self, value):
        if not str(value).isdigit(): return None
        n=int(value)
        local,total=(n-self.offset,n) if self.numbering=='absolute' else (n,n+self.offset)
        return (local,total) if local>0 else None

    def label(self, value):
        if not str(value).isdigit(): return '待核对'
        pair=self.pair(value)
        if not self.enabled or not pair: return str(value)
        return f'{pair[0]} / 总{pair[1]}'

    def display(self, value):
        if not value or not self.enabled: return value
        pair=self.pair(value)
        return pair[0 if self.range_basis=='local' else 1] if pair else value

    def storage(self, value):
        if not value or not self.enabled: return value
        if self.range_basis=='local' and self.numbering=='absolute': return value+self.offset
        if self.range_basis=='absolute' and self.numbering=='local': return value-self.offset
        return value


def source_health(reports, history=()):
    """Describe connectivity independently of whether a query found a release."""
    result=[]; prior={r['source']:r for r in history}
    for source in ('Nyaa','AnimeGarden','Mikan','DMHY'):
        rows=[dict(q,checked_at=q.get('checked_at',r.get('checked_at',0)))
              for r in reports for q in r.get('queries',[]) if q.get('source')==source]
        ok=[r for r in rows if not r.get('error') and not r.get('skipped')]
        bad=[r for r in rows if r.get('error')]
        status='尚未检查' if not rows else '部分可用' if ok and bad else '连接失败' if bad else '连接正常'
        result.append(dict(source=source,status=status,queries=len(rows),failures=len(bad),
                           returned=sum(r.get('returned',0) for r in ok),kept=sum(r.get('kept',0) for r in ok),
                           last_success=max(prior.get(source,{}).get('last_success',0),max((r.get('checked_at',0) for r in ok),default=0)),
                           last_attempt=max((r.get('checked_at',0) for r in rows),default=0)))
    return result


def save_source_health(path,reports):
    path=Path(path)
    try:previous=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):previous=[]
    latest=source_health(reports,previous)
    old={r['source']:r for r in previous}
    latest=[old.get(r['source'],r) if not r['queries'] else r for r in latest]
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(latest,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
    return latest


class SearchLimiter:
    """One request per host, minimum spacing and bounded backoff after repeated failures."""
    def __init__(self, interval=.25, clock=time.monotonic):
        self.interval=interval; self.clock=clock
        self.lock=threading.Lock(); self.hosts={}

    def request(self, url, operation, cancelled=None, report=None):
        host=urllib.parse.urlparse(url).hostname or ''
        with self.lock:
            state=self.hosts.setdefault(host,{'gate':threading.Lock(),'next':0,'failures':0})
        def stopped(): return bool(cancelled and cancelled.is_set())
        def pause(delay):
            if cancelled:
                if cancelled.wait(delay): raise InterruptedError('搜索已取消')
            else: time.sleep(delay)
        while not state['gate'].acquire(timeout=.1):
            if stopped(): raise InterruptedError('搜索已取消')
        try:
            if stopped(): raise InterruptedError('搜索已取消')
            delay=max(0,state['next']-self.clock())
            if delay and report is not None: report['wait_seconds']=round(delay,2)
            while self.clock()<state['next']:
                pause(min(.25,state['next']-self.clock()))
            try:
                result=operation()
                state['failures']=0; state['next']=self.clock()+self.interval
                return result
            except InterruptedError: raise
            except Exception as error:
                state['failures']+=1
                delay=min(30,2**min(state['failures']-2,5)) if state['failures']>=3 else self.interval
                retry_after=(getattr(error,'headers',None) or {}).get('Retry-After')
                if retry_after:
                    try: requested=float(retry_after)
                    except ValueError:
                        try: requested=(parsedate_to_datetime(retry_after)-datetime.now(timezone.utc)).total_seconds()
                        except (TypeError,ValueError): requested=0
                    delay=max(delay,min(300,max(0,requested)))
                state['next']=self.clock()+delay
                if report is not None: report['cooldown_seconds']=round(delay,2)
                raise
        finally: state['gate'].release()
