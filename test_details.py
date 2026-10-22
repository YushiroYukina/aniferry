import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from details import DetailCache

def subject_data(id=42):
    return {'id':id,'type':2,'name':'Test','name_cn':'测试动画','summary':'简介内容',
            'images':{'medium':'https://lain.bgm.tv/pic/cover/m/test.jpg'},
            'rating':{'score':8.2,'rank':123,'total':100,'count':{'10':30,'8':70}},'eps':12}

def png():
    output=io.BytesIO(); Image.new('RGB',(300,450),'#8ab7b8').save(output,format='PNG'); return output.getvalue()

class CacheTests(unittest.TestCase):
    def test_managed_ssl_hook_preserves_required_policy_file(self):
        import ssl,runpy
        hook=Path(__file__).parent/'tools/hooks/hook-ssl.py'
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'ssl.py';source.write_text("policy='codex-rejected-cas.pem'",encoding='utf-8')
            policy=source.with_name('codex-rejected-cas.pem');policy.write_text('fixture-policy',encoding='utf-8')
            with patch.object(ssl,'__file__',str(source)):
                data=runpy.run_path(str(hook))['datas']
            self.assertEqual(data,[(str(policy),'.')]);self.assertEqual(policy.read_text(),'fixture-policy')

    def test_managed_ssl_hook_fails_build_if_required_policy_is_missing(self):
        import ssl,runpy
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'ssl.py';source.write_text("policy='codex-rejected-cas.pem'",encoding='utf-8')
            with patch.object(ssl,'__file__',str(source)):
                with self.assertRaises(RuntimeError):runpy.run_path(str(Path(__file__).parent/'tools/hooks/hook-ssl.py'))

    def test_standard_ssl_hook_does_not_add_an_unneeded_policy(self):
        import ssl,runpy
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'ssl.py';source.write_text('class SSLContext: pass',encoding='utf-8')
            with patch.object(ssl,'__file__',str(source)):
                self.assertEqual(runpy.run_path(str(Path(__file__).parent/'tools/hooks/hook-ssl.py'))['datas'],[])

    def test_default_detail_fetch_retries_transient_network_failure(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as tmp:
            with patch('core.fetch',side_effect=[urllib.error.URLError('temporary EOF'),json.dumps(subject_data()).encode(),png()]) as request,patch('core.time.sleep'):
                data=DetailCache(tmp).get(42)
            self.assertEqual(data['score'],8.2);self.assertEqual(request.call_count,3)
            self.assertTrue(Path(data['cover_path']).is_file())

    def test_tls_check_cli_runs_without_opening_gui_or_using_network(self):
        import subprocess,sys
        with tempfile.TemporaryDirectory() as tmp:
            process=subprocess.run([sys.executable,str(Path(__file__).with_name('app.py')),'--tls-check','--data-dir',tmp],
                                   capture_output=True,timeout=15)
            self.assertEqual(process.returncode,0,process.stderr.decode(errors='replace'))
            data=json.loads((Path(tmp)/'tls-check.json').read_text(encoding='utf-8'))
            self.assertTrue(data['ok']);self.assertTrue(data['hostname_check']);self.assertEqual(data['verify_mode'],2)
            self.assertFalse((Path(tmp)/'tracker.sqlite3').exists())

    def test_construct_does_not_fetch_or_create_cache(self):
        with tempfile.TemporaryDirectory() as root:
            fetcher=Mock(); cache=DetailCache(root,fetcher)
            self.assertIsNone(cache.peek(42)); self.assertFalse(cache.root.exists()); fetcher.assert_not_called()

    def test_first_open_fetches_and_reopen_is_offline(self):
        with tempfile.TemporaryDirectory() as root:
            fetcher=Mock(side_effect=[json.dumps(subject_data()).encode(),png()]); cache=DetailCache(root,fetcher)
            first=cache.get(42); second=cache.get(42)
            self.assertEqual(fetcher.call_count,2); self.assertTrue(second['cache_hit'])
            self.assertEqual(second['score'],8.2); self.assertTrue(Path(first['cover_path']).is_file())
            with Image.open(first['cover_path']) as image: self.assertLessEqual(image.height,400)

    def test_force_refresh_and_expiry(self):
        with tempfile.TemporaryDirectory() as root:
            fetcher=Mock(side_effect=lambda url,**_:json.dumps(subject_data()).encode() if '/v0/' in url else png())
            cache=DetailCache(root,fetcher); cache.get(42); cache.get(42,force=True)
            self.assertEqual(fetcher.call_count,4)
            path,_=cache.paths(42); data=json.loads(path.read_text(encoding='utf-8')); data['cached_at']=time.time()-cache.TTL-1
            path.write_text(json.dumps(data),encoding='utf-8'); cache.get(42); self.assertEqual(fetcher.call_count,5)

    def test_failed_refresh_preserves_previous_cache(self):
        with tempfile.TemporaryDirectory() as root:
            fetcher=Mock(side_effect=[json.dumps(subject_data()).encode(),png(),TimeoutError('offline')])
            cache=DetailCache(root,fetcher); cache.get(42)
            with self.assertRaises(TimeoutError): cache.get(42,force=True)
            self.assertEqual(cache.peek(42)['score'],8.2)

    def test_invalid_subject_and_untrusted_cover(self):
        with tempfile.TemporaryDirectory() as root:
            fetcher=Mock(); cache=DetailCache(root,fetcher)
            with self.assertRaises(ValueError): cache.get('../secret')
            fetcher.assert_not_called()
            data=subject_data(); data['images']['medium']='https://example.org/untrusted.png'
            fetcher.return_value=json.dumps(data).encode(); cache.get(42)
            self.assertEqual(fetcher.call_count,1)

    def test_wrong_subject_is_not_cached(self):
        with tempfile.TemporaryDirectory() as root:
            cache=DetailCache(root,Mock(return_value=json.dumps(subject_data(43)).encode()))
            with self.assertRaises(ValueError): cache.get(42)
            self.assertIsNone(cache.peek(42))

    def test_five_hour_boundary_and_daily_restart(self):
        from datetime import datetime
        base=datetime.fromisoformat('2026-10-18T10:00:00+08:00').timestamp()
        with tempfile.TemporaryDirectory() as root:
            fetcher=Mock(side_effect=lambda url,**_:json.dumps(subject_data()).encode() if '/v0/' in url else png())
            cache=DetailCache(root,fetcher)
            with patch('details.time.time',return_value=base): cache.get(42,reason='daily')
            self.assertEqual(fetcher.call_count,2)
            with patch('details.time.time',return_value=base+5*3600-1): cache.get(42)
            self.assertEqual(fetcher.call_count,2)
            with patch('details.time.time',return_value=base+5*3600):
                restarted=DetailCache(root,fetcher); restarted.get(42,reason='daily')
                self.assertEqual(fetcher.call_count,2)
                restarted.get(42)
            self.assertEqual(fetcher.call_count,3)

    def test_new_day_refreshes_even_inside_five_hours(self):
        from datetime import datetime
        base=datetime.fromisoformat('2026-10-18T23:50:00+08:00').timestamp()
        with tempfile.TemporaryDirectory() as root:
            fetcher=Mock(side_effect=lambda url,**_:json.dumps(subject_data()).encode() if '/v0/' in url else png()); cache=DetailCache(root,fetcher)
            with patch('details.time.time',return_value=base): cache.get(42)
            with patch('details.time.time',return_value=base+15*60):
                cache.get(42,reason='daily'); cache.get(42,reason='daily')
            self.assertEqual(fetcher.call_count,3)
            self.assertEqual(cache.day(base+15*60),'2026-10-19')

    def test_concurrent_initial_requests_share_one_fetch(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as root:
            fetcher=Mock(side_effect=lambda url,**_:json.dumps(subject_data()).encode() if '/v0/' in url else png()); cache=DetailCache(root,fetcher)
            with ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(cache.get,42); b=pool.submit(cache.get,42)
                a.result(); b.result()
            self.assertEqual(fetcher.call_count,2)

if __name__=='__main__': unittest.main()
