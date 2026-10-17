import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core import *

def release(n, ep='1', ani=False, seeds=10):
    return dict(hash=str(n).zfill(40), title=f'[ANI] Test - {ep}' if ani else f'Test - {ep}',
                episode=ep, ani=ani, seeds=seeds, subtitle='未知', size='1 GiB',url='https://nyaa.si/download/123.torrent')

class Tests(unittest.TestCase):
    def test_rank_by_episode(self):
        items=[release(i,seeds=i) for i in range(1,7)]+[release(7,'2',True,0),release(8,'2',False,999)]
        got=recommend(items,set())
        self.assertEqual([x['hash'] for x in got],[str(i).zfill(40) for i in [6,5,4,7]])
        self.assertEqual(len(recommend(items,{'1'})),1)

    def test_subtitle_no_guess(self):
        self.assertEqual(subtitle('[ANI] Anime - 01'), '未知')
        self.assertEqual(subtitle('[CHS] Anime'), '标题声明中文')
        self.assertEqual(subtitle('Anime English only'), '无中文')
        self.assertEqual(subtitle('Anime [SubsPlease]'), '未知')

    def test_episode(self):
        for title in ['[ANI] Test - 01 [1080P]', '[Group] Test [01v2][1080p]', 'Test 第01集', 'Test E01']:
            self.assertEqual(episode(title),'1')
        self.assertTrue(episode('Test 2026 [1080p]').startswith('待核对'))

    def test_rss(self):
        xml=b'<rss xmlns:n="https://nyaa.si/xmlns/nyaa"><channel><item><title>[ANI] Test - 01 [CHS]</title><n:infoHash>aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</n:infoHash><n:seeders>42</n:seeders></item></channel></rss>'
        item=parse_rss(xml)[0]
        self.assertEqual(item['seeds'],42); self.assertTrue(item['ani'])

    def test_validate_torrent(self):
        info=b'd4:name4:teste'; data=b'd4:info'+info+b'e'; hash_=hashlib.sha1(info).hexdigest()
        validate_torrent(data,hash_)
        for bad in [b'<html>blocked</html>', data+b'garbage', b'd4:info5:abc']:
            with self.assertRaises(ValueError): validate_torrent(bad,hash_)
        with self.assertRaises(ValueError): validate_torrent(data,'0'*40)

    def test_persistence_and_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp); s.save_show('测试',{'中文名':'测试','英文名':'Test'})
            s.ingest(1,[release(1),release(2,seeds=100)])
            s.db.execute("UPDATE releases SET status='已转交',output='E:/动画' WHERE hash=?",(release(1)['hash'],)); s.db.commit()
            self.assertEqual(s.candidates(1),[])
            s.ingest(1,[release(1)])
            self.assertEqual(s.candidates(1),[])
            queue=json.loads(s.export_queue().read_text(encoding='utf-8'))
            self.assertEqual(queue[0]['subtitle'],'未知')
            self.assertEqual(queue[0]['state'],'待确认下载完成及字幕轨道')
            s.db.close()

    def test_transfer_arguments_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp); s.save_show('测试',{'中文名':'测试','英文名':'Test'})
            exe=Path(tmp)/'BitComet.exe'; exe.touch(); s.set_setting('bitcomet',str(exe))
            s.set_setting('output',str(Path(tmp)/'动画'))
            info=b'd4:name4:teste'; data=b'd4:info'+info+b'e'
            item=release(1); item['hash']=hashlib.sha1(info).hexdigest(); s.ingest(1,[item])
            with patch('core.fetch',return_value=data),patch('core.subprocess.Popen') as launch:
                s.transfer(1,item)
                args=launch.call_args.args[0]
                self.assertEqual(args[0],str(exe)); self.assertTrue(args[2].startswith('--output=')); self.assertEqual(args[3],'--silent')
                with self.assertRaises(ValueError): s.transfer(1,item)
                self.assertEqual(launch.call_count,1)
                self.assertEqual(s.db.execute('SELECT status FROM releases').fetchone()[0],'待接收')
                self.assertTrue((Path(tmp)/'动画'/'_种子'/(item['hash']+'.torrent')).is_file())
                self.assertEqual(s.sync_received({item['hash']:{'SaveDirectory':str(Path(tmp)/'错误目录')}}),0)
                self.assertEqual(s.sync_received({item['hash']:{'SaveDirectory':str(Path(tmp)/'动画')}}),1)
                self.assertEqual(s.db.execute('SELECT status FROM releases').fetchone()[0],'已转交')
            s.db.close()

    def test_anilist_names_and_season_match(self):
        raw={'data':{'Page':{'media':[
            {'title':{'native':'作品 第2期','romaji':'Wrong season','english':'Wrong season'}},
            {'title':{'native':'作品','romaji':'Sakuhin','english':'Work'}}]}}}
        with patch('core.fetch',return_value=json.dumps(raw).encode()) as fetch_:
            self.assertEqual(complete_aliases(' 作 品 '),('Sakuhin','Work'))
            self.assertEqual(fetch_.call_count,1)

    def test_alias_fallback_and_no_invented_english(self):
        raw={'data':[{'title_japanese':'作品','title':'Sakuhin','title_english':None}]}
        with patch('core.fetch',side_effect=[TimeoutError('offline'),json.dumps(raw).encode()]):
            self.assertEqual(complete_aliases('作品'),('Sakuhin',''))

    def test_bulk_preserves_manual_names_and_disambiguates(self):
        before={'中文名':'测试','日文名':'作品','罗马音':'My custom title','英文名':''}
        with patch('core.complete_aliases',return_value=('Sakuhin','Work')):
            self.assertEqual(fill_missing_aliases('测试',before)['罗马音'],'My custom title')
            self.assertEqual(fill_missing_aliases('测试',before)['英文名'],'Work')
        with patch('core.lookup',return_value=[{'中文名':'测试','日文名':'一'},{'中文名':'测试','日文名':'二'}]):
            with self.assertRaises(ValueError): fill_missing_aliases('测试',{})

    def test_completion_cutoff_persists_and_keeps_previous_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp); sid=s.save_show('测试',{'中文名':'测试','英文名':'Test'})
            s.ingest(sid,[release(i,str(i)) for i in range(1,8)])
            s.db.execute("UPDATE releases SET status='已转交' WHERE episode='5'"); s.db.commit()
            # Merely handing off episode 5 must not retire earlier episodes.
            self.assertIn('1',[x['episode'] for x in s.candidates(sid)])
            self.assertEqual(s.sync_completed({release(5)['hash']}),1)
            self.assertEqual(s.episode_rule(sid),(4,0,5))
            self.assertEqual([x['episode'] for x in s.candidates(sid)],['4','6','7'])
            self.assertEqual(s.db.execute("SELECT COUNT(*) FROM releases WHERE status='弃置'").fetchone()[0],3)
            s.ingest(sid,[release(99,'2')])
            self.assertIsNone(s.db.execute('SELECT 1 FROM releases WHERE hash=?',(release(99)['hash'],)).fetchone())
            s.db.close(); s=Store(tmp)
            self.assertEqual(s.episode_rule(sid),(4,0,5))
            s.set_episode_range(sid,1,6)
            self.assertEqual([x['episode'] for x in s.candidates(sid)],['4','6'])
            self.assertIn('下载完成',json.loads(s.export_queue().read_text(encoding='utf-8'))[0]['state'])
            s.db.close()

    def test_task_file_rejects_partial_and_incomplete_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml=Path(tmp)/'Downloads.xml'
            good='a'*40; partial='b'*40; unfinished='c'*40
            xml.write_text(f'<BitComet><Torrents><Torrent InfoHashHex="{good.upper()}" Size="100" SelectedSize="100" Left="0" FinishDate="2026-10-13 12:00:00"/><Torrent InfoHashHex="{partial}" Size="100" SelectedSize="20" Left="0" FinishDate="2026-10-13"/><Torrent InfoHashHex="{unfinished}" Size="100" SelectedSize="100" Left="50" FinishDate="2026-10-13"/></Torrents></BitComet>')
            self.assertEqual(completed_hashes(xml),{good})
            xml.write_text('<BitComet>')
            with self.assertRaises(ET.ParseError): completed_hashes(xml)

    def test_range_and_search_discard_old_results(self):
        raw=b'<rss xmlns:n="https://nyaa.si/xmlns/nyaa"><channel><item><title>Test - 02</title><n:infoHash>aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</n:infoHash></item><item><title>Test - 04</title><n:infoHash>bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb</n:infoHash></item><item><title>Test - 07</title><n:infoHash>cccccccccccccccccccccccccccccccccccccccc</n:infoHash></item></channel></rss>'
        with patch('core.fetch',return_value=raw) as fetch_,patch('core.time.sleep'):
            items,errors=search_show({'英文名':'Test'},first=4,last=6,use_garden=False)
            self.assertEqual([x['episode'] for x in items],['4']); self.assertEqual(errors,[])
            query=urllib.parse.parse_qs(urllib.parse.urlparse(fetch_.call_args.args[0]).query)['q'][0]
            self.assertIn('-" - 02"',query)
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp)
            with self.assertRaises(ValueError): s.set_episode_range(1,5,3)
            s.db.close()

    def test_garden_identity_type_and_ani(self):
        good={'subjectId':42,'type':'动画','title':'【ANi】 测试 - 02 [CHT]','magnet':'magnet:?xt=urn:btih:'+base64.b32encode(bytes.fromhex('a'*40)).decode(),'provider':'dmhy','providerId':'123','size':1024,'fansub':{'name':'ANi'}}
        wrong=dict(good,subjectId=43); music=dict(good,type='音乐')
        items=parse_garden(json.dumps({'status':'OK','resources':[good,wrong,music]}).encode(),42)
        self.assertEqual(len(items),1); self.assertEqual(items[0]['hash'],'a'*40)
        self.assertTrue(items[0]['ani']); self.assertFalse(items[0]['seed_known'])
        self.assertFalse(allowed_torrent_url(items[0]['url'],items[0]['hash']))
        self.assertTrue(allowed_torrent_url('https://mikanani.me/Download/20261013/'+'a'*40+'.torrent',items[0]['hash']))
        self.assertFalse(allowed_torrent_url('https://mikanani.me/Download/20261013/'+'b'*40+'.torrent',items[0]['hash']))
        self.assertFalse(allowed_torrent_url('https://evil.example/test.torrent',items[0]['hash']))

    def test_cross_source_dedup_prefers_nyaa_and_preserves_ani(self):
        a=release(1); a.update(source='Nyaa',seed_known=True,url='https://nyaa.si/download/123.torrent')
        b=dict(a,source='AnimeGarden',seed_known=False,seeds=0,ani=True,url='https://mikanani.me/Home/Episode/'+a['hash'])
        for first,second in [(a,b),(b,a)]:
            merged={}; merge_release(merged,first); merge_release(merged,second)
            self.assertEqual(len(merged),1); self.assertEqual(merged[a['hash']]['url'],a['url'])
            self.assertEqual(merged[a['hash']]['seeds'],10); self.assertTrue(merged[a['hash']]['ani'])

    def test_search_diagnostics_distinguishes_failure_zero_and_filtered(self):
        raw=b'<rss xmlns:n="https://nyaa.si/xmlns/nyaa"><channel><item><title>Test - 02</title><n:infoHash>aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</n:infoHash></item></channel></rss>'
        with patch('core.fetch',side_effect=[raw,b'<rss><channel/></rss>',TimeoutError('timeout')]),patch('core.time.sleep'):
            reports=[]
            items,errors=search_show({'中文名':'a','日文名':'b','罗马音':'c','_bgm_id':'42'},first=4,diagnostics=reports,use_garden=False)
            self.assertEqual(items,[]); self.assertEqual(len(errors),1); self.assertEqual(len(reports),3)
            self.assertEqual((reports[0]['returned'],reports[0]['kept']),(1,0))
            self.assertEqual((reports[1]['returned'],reports[1]['kept']),(0,0))
            self.assertTrue(reports[2]['error'])

    def test_new_episode_formats_and_subtitle_markers(self):
        self.assertEqual(episode('Test S02E03 [1080p]'),'3')
        self.assertTrue(episode('Test [01-12][1080p]').startswith('待核对'))
        self.assertEqual(subtitle('Test [简繁日内封]'),'标题声明中文')
        self.assertEqual(subtitle('Test [簡日雙語]'),'标题声明中文')

    def test_resolve_supplement_torrent_checks_hash_and_host(self):
        hash_='a'*40
        item={'hash':hash_,'url':'https://mikanani.me/Home/Episode/'+hash_}
        html=f'<a href="https://evil.example/a.torrent">bad</a><a href="/Download/20261013/{hash_}.torrent">download</a>'
        with patch('core.fetch',return_value=html.encode()):
            self.assertEqual(resolve_torrent_url(item),'https://mikanani.me/Download/20261013/'+hash_+'.torrent')

    def test_precise_identity_rejects_real_false_positives(self):
        aliases={'中文名':'女友的朋友','日文名':'彼女の友達','罗马音':'Kanojo no Tomodachi','英文名':"My Girlfriend's Friend",'_bgm_id':'647220'}
        for title in ['[ANi] 超超超超超喜歡你的100個女朋友 - 36 [CHT]',
                      'Gekkan Shoujo Nozaki-kun 月刊少女野崎くん 特典アニメ 彼氏',
                      '[LoliHouse] 我和班上第二可爱的女生成为朋友 - 12']:
            self.assertFalse(matches_show({'title':title},aliases))
        for title in ['[ANi] 女友的朋友 - 02 [CHT]', '[Group] Kanojo no Tomodachi S01E02',
                      '[Group] My Girlfriends Friend S01E02', '[Group] 彼女の友达 [02]']:
            self.assertTrue(matches_show({'title':title},aliases))
        self.assertFalse(matches_show({'title':'女友的朋友','subject_id':'123'},aliases))
        self.assertFalse(matches_show({'title':'Test Season 2 - 01'},{'英文名':'Test'}))
        self.assertFalse(matches_show({'title':'Contest - 01'},{'英文名':'Test'}))
        self.assertTrue(matches_show({'title':'冰之城牆 - 01'},{'中文名':'冰之城墙'}))

    def test_delete_does_not_delete_files_or_accept_late_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp); sid=s.save_show('测试',{'英文名':'Test'})
            s.ingest(sid,[release(1)]); s.set_episode_range(sid,1,3)
            video=Path(tmp)/'movie.mp4'; video.write_bytes(b'video')
            torrent=Path(tmp)/'torrent.torrent'; torrent.write_bytes(b'torrent')
            s.delete_show(sid)
            self.assertEqual(s.shows(),[])
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM releases').fetchone()[0],0)
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM episode_rules').fetchone()[0],0)
            s.ingest(sid,[release(2)])
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM releases').fetchone()[0],0)
            fresh=s.save_show('重加',{'英文名':'Test'}); self.assertGreater(fresh,sid)
            self.assertEqual(video.read_bytes(),b'video'); self.assertEqual(torrent.read_bytes(),b'torrent')
            self.assertTrue(list(Path(tmp).glob('tracker-before-delete-*.sqlite3')))
            s.db.close()

    def test_external_rss_mikan_and_dmhy(self):
        hash_='a'*40
        for source,url in [('Mikan','https://mikanani.me/Download/20261018/'+hash_+'.torrent'),('DMHY','magnet:?xt=urn:btih:'+hash_)]:
            raw=f'<rss><channel><item><title>Test - 02 [CHS]</title><link>https://example.org/view</link><enclosure url="{url}" length="1024"/></item></channel></rss>'.encode()
            items=parse_external_rss(raw,source)
            self.assertEqual(len(items),1); self.assertEqual(items[0]['hash'],hash_)
            self.assertEqual(items[0]['episode'],'2'); self.assertFalse(items[0]['seed_known'])

    def test_mismatch_does_not_enter_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp); sid=s.save_show('女友的朋友',{'中文名':'女友的朋友'})
            bad=release(1); good=dict(release(2),title='女友的朋友 - 01')
            s.ingest(sid,[bad,good]); self.assertEqual(len(s.candidates(sid)),1)
            self.assertEqual(s.candidates(sid)[0]['hash'],good['hash']); s.db.close()

    def test_batch_delete_uses_one_backup_and_does_not_resurrect(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp)
            try:
                ids=[s.save_show('Test '+str(i),{'英文名':'Test'}) for i in range(3)]
                for sid in ids:
                    s.ingest(sid,[release(1)]); s.set_episode_range(sid,1,9)
                    s.db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)',(sid,release(1)['hash'],'aria2','{}',''))
                s.db.commit(); video=Path(tmp)/'original.mp4'; video.write_bytes(b'original')
                self.assertEqual(s.delete_shows(ids[:2]),2)
                self.assertEqual([r[0] for r in s.shows()],[ids[2]])
                self.assertEqual(len(list(Path(tmp).glob('tracker-before-delete-*.sqlite3'))),1)
                self.assertEqual(s.db.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0],1)
                for sid in ids[:2]: s.ingest(sid,[release(2)])
                self.assertEqual(s.db.execute('SELECT COUNT(*) FROM releases').fetchone()[0],1)
                self.assertEqual(video.read_bytes(),b'original')
            finally: s.db.close()

    def test_clear_unknown_scoped_persistent_and_keeps_download_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp)
            try:
                a=s.save_show('Test A',{'英文名':'Test'}); b=s.save_show('Test B',{'英文名':'Test'})
                unknown=[dict(release(i),title='Test special '+str(i),episode='待核对:Test special '+str(i)) for i in range(1,5)]
                for sid in (a,b): s.ingest(sid,unknown+[release(5,'5')])
                s.db.execute("UPDATE releases SET status='已转交' WHERE show_id=? AND hash=?",(a,unknown[1]['hash']))
                s.db.execute("UPDATE releases SET status='已完成' WHERE show_id=? AND hash=?",(a,unknown[2]['hash']))
                s.db.execute("UPDATE releases SET status='忽略' WHERE show_id=? AND hash=?",(a,unknown[3]['hash'])); s.db.commit()
                self.assertEqual(s.clear_unknown(a),2); s.ingest(a,unknown)
                statuses=dict(s.db.execute('SELECT hash,status FROM releases WHERE show_id=?',(a,)))
                self.assertEqual(statuses[unknown[0]['hash']],'已清理'); self.assertEqual(statuses[unknown[1]['hash']],'已转交')
                self.assertEqual(statuses[unknown[2]['hash']],'已完成'); self.assertEqual(statuses[release(5)['hash']],'待确认')
                self.assertEqual(len(s.candidates(b)),5)
                s.db.close(); s=Store(tmp)
                self.assertFalse(any(x['episode'].startswith('待核对') for x in s.candidates(a)))
            finally: s.db.close()

    def test_batch_ignore_cannot_change_accepted_or_other_shows(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp)
            try:
                a=s.save_show('Test A',{'英文名':'Test'}); b=s.save_show('Test B',{'英文名':'Test'})
                for sid in (a,b): s.ingest(sid,[release(i,str(i)) for i in range(1,4)])
                s.db.execute("UPDATE releases SET status='待接收' WHERE show_id=? AND hash=?",(a,release(3)['hash'])); s.db.commit()
                self.assertEqual(s.ignore_releases(a,[release(i)['hash'] for i in range(1,4)]),2)
                self.assertEqual(s.db.execute("SELECT COUNT(*) FROM releases WHERE show_id=? AND status='待接收'",(a,)).fetchone()[0],1)
                self.assertEqual(len(s.candidates(b)),3)
                s.ingest(a,[release(1,'1')]); self.assertEqual(s.db.execute('SELECT status FROM releases WHERE show_id=? AND hash=?',(a,release(1)['hash'])).fetchone()[0],'忽略')
            finally: s.db.close()

if __name__=='__main__': unittest.main()
