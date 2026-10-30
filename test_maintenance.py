import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from unittest.mock import patch, Mock
from core import Store, search_show
from maintenance import subscription_url
from test_core import release


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.sid=self.store.save_show('Test',{'英文名':'Test'})

    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()

    def finish_six(self):
        self.store.ingest(self.sid,[release(i,str(i)) for i in range(1,7)])
        self.store.db.execute("UPDATE releases SET status='已转交' WHERE episode='6'");self.store.db.commit()
        self.store.mark_completed(self.sid,release(6)['hash'])

    def test_missing_inventory_does_not_treat_highwater_as_all_completed(self):
        self.finish_six()
        rows=dict(self.store.episode_inventory(self.sid))
        self.assertEqual(rows[3],'旧候选已隐藏');self.assertEqual(rows[6],'已完成')
        self.store.db.execute("DELETE FROM releases WHERE episode='3'");self.store.db.commit()
        self.assertEqual(dict(self.store.episode_inventory(self.sid))[3],'未找到')
        self.assertNotIn(7,rows)
        self.assertEqual(dict(self.store.episode_inventory(self.sid,8))[8],'尚无记录')

    def test_manual_repair_revives_only_selected_discarded_episode_and_not_main(self):
        self.finish_six();self.store.request_repair(self.sid,{3})
        self.assertNotIn('3',{i['episode'] for i in self.store.candidates(self.sid)})
        self.assertEqual([i['episode'] for i in self.store.candidates(self.sid,episodes={3},expanded=True)],['3'])
        self.assertEqual(self.store.episode_rule(self.sid)[0],5)
        self.store.end_repair(self.sid,{3});self.assertEqual(self.store.candidates(self.sid,episodes={3}),[])

    def test_low_episode_ingest_requires_explicit_repair(self):
        self.finish_six();item=release(90,'2');self.store.ingest(self.sid,[item])
        self.assertIsNone(self.store.db.execute('SELECT 1 FROM releases WHERE hash=?',(item['hash'],)).fetchone())
        self.store.request_repair(self.sid,{2});self.store.ingest(self.sid,[item])
        self.assertIn(item['hash'],{i['hash'] for i in self.store.candidates(self.sid,episodes={2},expanded=True)})

    def test_completing_repaired_episode_does_not_lower_progress(self):
        self.finish_six();self.store.request_repair(self.sid,{3})
        self.store.db.execute("UPDATE releases SET status='已转交' WHERE episode='3'");self.store.db.commit()
        self.store.mark_completed(self.sid,release(3)['hash'])
        self.assertEqual(self.store.episode_rule(self.sid)[2],6);self.assertEqual(self.store.repair_episodes(self.sid),set())

    def test_feedback_persists_after_rescan_and_is_per_show_reversible(self):
        other=self.store.save_show('Test copy',{'英文名':'Test'});item=release(1)
        for sid in (self.sid,other):self.store.ingest(sid,[item])
        self.store.correct_releases(self.sid,[item['hash']],'不是本作')
        self.store.ingest(self.sid,[item]);self.assertEqual(self.store.candidates(self.sid),[])
        self.assertEqual(len(self.store.candidates(other)),1)
        self.store.restore_candidates(self.sid,[item['hash']]);self.assertEqual(len(self.store.candidates(self.sid)),1)

    def test_repair_does_not_revive_user_correction_or_completed_history(self):
        self.store.ingest(self.sid,[release(1)]);self.store.correct_releases(self.sid,[release(1)['hash']],'季度不符')
        self.store.request_repair(self.sid,{1});self.assertEqual(self.store.candidates(self.sid,episodes={1}),[])
        with self.assertRaisesRegex(ValueError,'已被处理'):self.store.transfer(self.sid,release(1))

    def test_clear_unknown_can_be_restored(self):
        item=dict(release(1),episode='待核对:Test pack');self.store.ingest(self.sid,[item])
        self.store.clear_unknown(self.sid);self.assertEqual(self.store.candidates(self.sid),[])
        self.store.restore_candidates(self.sid,[item['hash']]);self.assertEqual(len(self.store.candidates(self.sid)),1)

    def test_subscription_strict_allowlist(self):
        self.assertEqual(subscription_url('https://mikanani.me/RSS/Bangumi?bangumiId=42')[1],'Mikan')
        for url in ('http://nyaa.si/?page=rss','https://nyaa.si.evil.test/?page=rss','https://localhost/rss','https://u:p@nyaa.si/?page=rss','https://nyaa.si/?page=rss&token=abc','https://mikanani.me/RSS/MyBangumi?token=x'):
            with self.subTest(url=url),self.assertRaises(ValueError):subscription_url(url)

    def test_bound_feed_keeps_identity_and_target_episode_filter(self):
        raw=b'<rss xmlns:n="https://nyaa.si/xmlns/nyaa"><channel>'+b''.join(
            f'<item><title>{title}</title><n:infoHash>{str(i).zfill(40)}</n:infoHash></item>'.encode()
            for i,title in enumerate(('Test - 02','Other - 02','Test - 03'),1))+b'</channel></rss>'
        feed='https://nyaa.si/?page=rss&q=bound';reports=[]
        with patch('core.fetch',side_effect=lambda url,**_:raw if url==feed else b'<rss><channel/></rss>') as fetch:
            items,_=search_show({'英文名':'Test'},first=2,last=3,use_garden=False,use_external=False,subscriptions=[feed],target_episodes={2},diagnostics=reports)
        self.assertEqual([i['title'] for i in items],['Test - 02'])
        self.assertTrue(any(r['source']=='Subscription' and r['identity_rejected']==1 and r['range_rejected']==1 for r in reports))

    def test_task_reset_requires_fresh_absence_not_offline_or_existing(self):
        item=release(1);self.store.ingest(self.sid,[item]);h=item['hash']
        self.store.db.execute("UPDATE releases SET status='待接收'");self.store.db.commit()
        for state in ('连接异常','目录不符','已暂停'):
            self.store.observe_task(self.sid,h,state)
            with self.assertRaises(ValueError):self.store.reset_handoff_checked(self.sid,h)
        self.store.observe_task(self.sid,h,'未发现任务');self.store.db.execute('UPDATE task_observations SET checked=?',(time.time()-100,));self.store.db.commit()
        with self.assertRaises(ValueError):self.store.reset_handoff_checked(self.sid,h)
        self.store.observe_task(self.sid,h,'未发现任务');self.store.reset_handoff_checked(self.sid,h)
        self.assertEqual(len(self.store.candidates(self.sid)),1)

    def test_poll_reports_pause_and_connection_errors_without_resubmitting(self):
        item=release(1);h=item['hash'];self.store.ingest(self.sid,[item])
        self.store.db.execute("UPDATE releases SET status='待接收',output=?",(self.tmp.name,))
        self.store.db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)',(self.sid,h,'qbittorrent','{"url":"http://127.0.0.1:1"}',''));self.store.db.commit()
        adapter=Mock();adapter.snapshot.return_value={h:dict(directory=self.tmp.name,complete=False,state='已暂停')}
        with patch('downloaders.client',return_value=adapter):self.store.poll_downloaders()
        self.assertEqual(self.store.task_history()[0]['state'],'已暂停');adapter.add.assert_not_called()
        adapter.snapshot.side_effect=ValueError('offline')
        with patch('downloaders.client',return_value=adapter):self.store.poll_downloaders()
        self.assertEqual(self.store.task_history()[0]['state'],'连接异常');adapter.add.assert_not_called()

    def test_pending_timeout_label_and_legacy_unknown_age(self):
        item=release(1);self.store.ingest(self.sid,[item]);self.store.db.execute("UPDATE releases SET status='待接收'");self.store.db.commit()
        self.store.observe_task(self.sid,item['hash'],'未发现任务')
        self.assertEqual(self.store.task_history()[0]['state'],'等待接收')
        self.store.note_submission(self.sid,item['hash']);self.store.observe_task(self.sid,item['hash'],'未发现任务')
        self.store.db.execute('UPDATE task_observations SET submitted=?',(time.time()-150,));self.store.db.commit()
        self.assertEqual(self.store.task_history()[0]['state'],'长时间未接收')

    def test_backup_restore_preserves_before_copy_and_recreates_new_schema(self):
        backup=self.store.backup('manual');self.store.save_show('Second',{'英文名':'Second'})
        n,before=self.store.restore_backup(backup)
        self.assertEqual(n,1);self.assertEqual(len(self.store.shows()),1)
        self.assertEqual(self.store.validate_backup(before),2)
        self.store.request_repair(self.sid,{2});self.assertEqual(self.store.repair_episodes(self.sid),{2})

    def test_invalid_backup_never_overwrites_active_database(self):
        p=Path(self.tmp.name)/'invalid.sqlite3';p.write_text('invalid')
        with self.assertRaises((ValueError,sqlite3.DatabaseError)):self.store.restore_backup(p)
        self.assertEqual(len(self.store.shows()),1)

    def test_public_export_excludes_secrets_and_import_deduplicates(self):
        self.store.set_setting('secret','do-not-export')
        self.store.set_show_options(self.sid,{'subscriptions':['https://nyaa.si/?page=rss&q=private-preference']})
        p=Path(self.tmp.name)/'list.json';self.store.export_library(p);raw=p.read_text(encoding='utf-8')
        self.assertNotIn('do-not-export',raw);self.assertNotIn('private-preference',raw)
        self.assertEqual(self.store.import_library(p),0)
        self.store.delete_show(self.sid);self.assertEqual(self.store.import_library(p),1)

    def test_import_validates_whole_list_before_changing_data(self):
        p=Path(self.tmp.name)/'list.json';self.store.export_library(p);obj=json.loads(p.read_text(encoding='utf-8'))
        obj['shows'][0]['name']='New';obj['shows'].append({'name':'Invalid','aliases':{},'options':{'offset':-1}})
        p.write_text(json.dumps(obj),encoding='utf-8')
        with self.assertRaises(ValueError):self.store.import_library(p)
        self.assertEqual([r[1] for r in self.store.shows()],['Test'])

    def test_deletion_cleans_new_per_show_records(self):
        self.store.ingest(self.sid,[release(1)]);self.store.correct_releases(self.sid,[release(1)['hash']],'不是本作')
        self.store.request_repair(self.sid,{2});self.store.observe_task(self.sid,release(1)['hash'],'未发现任务')
        self.store.delete_show(self.sid)
        for table in ('corrections','repair_targets','task_observations'):
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_new_completion_does_not_discard_active_repair_candidates(self):
        self.finish_six();self.store.request_repair(self.sid,{3})
        self.store.ingest(self.sid,[release(7,'7')]);self.store.db.execute("UPDATE releases SET status='已转交' WHERE episode='7'");self.store.db.commit()
        self.store.mark_completed(self.sid,release(7)['hash'])
        self.assertEqual([i['episode'] for i in self.store.candidates(self.sid,episodes={3})],['3'])

    def test_first_upgrade_creates_backup_before_new_tables(self):
        self.store.db.close()
        with closing(sqlite3.connect(Path(self.tmp.name)/'tracker.sqlite3')) as db,db:
            for table in ('repair_targets','corrections','task_observations'):db.execute('DROP TABLE '+table)
        self.store=Store(self.tmp.name)
        backups=list(Path(self.tmp.name).glob('tracker-before-v21-*.sqlite3'));self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='repair_targets'").fetchone())


if __name__=='__main__':unittest.main()
