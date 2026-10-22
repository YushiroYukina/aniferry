import tempfile
import threading
import time
import tkinter as tk
import unittest
from tkinter import ttk
from unittest.mock import patch
from app import App

def button(parent, label):
    for widget in parent.winfo_children():
        if isinstance(widget,ttk.Button) and widget.cget('text') == label: return widget
        found=button(widget,label)
        if found: return found

class UITests(unittest.TestCase):
    def test_background_candidate_refresh_preserves_selection(self):
        from test_core import release
        sid=self.app.store.save_show('Test',{'英文名':'Test'})
        self.app.store.ingest(sid,[release(1,'1'),release(2,'2')]);self.app.refresh_shows()
        self.app.tree.selection_set(release(2)['hash'])
        self.app.store.ingest(sid,[release(3,'3')]);self.app.refresh_candidates()
        self.assertEqual(self.app.tree.selection(),(release(2)['hash'],))

    def test_cour_options_prefill_cached_offset_but_do_not_enable_dual(self):
        import json
        sid=self.app.store.save_show('Test Part 2',{'英文名':'Test Part 2','_bgm_id':'42'})
        cache=self.app.store.root/'search-cache';cache.mkdir()
        (cache/'cour-42.json').write_text(json.dumps({'subject':'42','span':[13,24],'checked_at':time.time()}),encoding='utf-8')
        self.app.refresh_shows()
        with patch('library_ui.cached_cour_span',side_effect=AssertionError('Opening settings must not fetch')):
            self.app.library_tools.preferences()
        win=self.app.library_tools.windows[('preferences',sid)]
        self.assertFalse(win.dual_var.get());self.assertEqual(win.offset_var.get(),'12')
        self.assertEqual(self.app.store.show_options(sid),{})

    def test_dual_numbering_is_opt_in_and_range_uses_selected_basis(self):
        import json
        from test_core import release
        sid=self.app.store.save_show('Test Part 2',{'英文名':'Test Part 2'})
        self.app.store.ingest(sid,[dict(release(1,'15'),title='Test Part 2 - 15')])
        self.app.store.set_episode_range(sid,13,24);self.app.refresh_shows()
        self.assertEqual(self.app.tree.item(release(1)['hash'],'values')[0],'15')
        self.app.library_tools.preferences();self.root.update()
        win=self.app.library_tools.windows[('preferences',sid)]
        win.dual_var.set(True);win.offset_var.set('12');win.update_mapping()
        self.assertEqual(win.first_var.get(),'1');self.assertEqual(win.last_var.get(),'12')
        win.first_var.set('3');win.last_var.set('3');button(win,'保存').invoke()
        self.assertEqual(self.app.store.episode_rule(sid)[:2],(15,15))
        self.assertEqual(self.app.tree.item(release(1)['hash'],'values')[0],'3 / 总15')
        self.assertEqual(self.app.first_ep.get(),'3')
        self.assertEqual(self.app.store.db.execute('select episode from releases').fetchone()[0],'15')
        self.app.store.set_show_options(sid,dict(self.app.store.show_options(sid),dual=False));self.app.refresh_candidates()
        self.assertEqual(self.app.tree.item(release(1)['hash'],'values')[0],'15')

    def test_independent_show_hides_dual_control_until_classified(self):
        self.app.store.save_show('Test',{'英文名':'Test'});self.app.refresh_shows();self.app.library_tools.preferences();self.root.update()
        win=self.app.library_tools.windows[('preferences',1)]
        self.assertFalse(win.continuation_var.get());self.assertFalse(win.dual_var.get())
        win.continuation_var.set(True);win.update_mapping();win.dual_var.set(True);win.offset_var.set('12');win.update_mapping()
        button(win,'保存').invoke();self.assertTrue(self.app.store.show_options(1)['dual'])

    def test_expanded_candidates_transfer_keeps_dialog_show_context(self):
        from test_core import release
        a=self.app.store.save_show('Test',{'英文名':'Test'});b=self.app.store.save_show('Other',{'英文名':'Other'})
        self.app.store.ingest(a,[release(n,ani=n==1) for n in range(1,6)]);self.app.refresh_shows()
        self.app.library_tools.candidates();win=self.app.library_tools.windows[('candidates',a)]
        self.assertEqual(len(self.app.tree.get_children()),1);self.assertEqual(len(win.candidate_tree.get_children()),5)
        win.candidate_tree.selection_set(release(2)['hash'])
        self.app.shows.selection_clear(0,'end');self.app.shows.selection_set(1);self.app.refresh_candidates()
        with patch.object(self.app,'transfer') as transfer:
            button(win,'转交所选').invoke()
        self.assertEqual(transfer.call_args.args[0],a);self.assertEqual(transfer.call_args.args[1]['hash'],release(2)['hash'])

    def test_selected_scan_preserves_other_show_diagnostics(self):
        import json
        a=self.app.store.save_show('A',{'英文名':'A'});b=self.app.store.save_show('B',{'英文名':'B'})
        path=self.app.store.root/'last-search.json';path.write_text(json.dumps([{'show_id':b,'name':'B','queries':[],'count':7,'ani':0}]),encoding='utf-8')
        self.app.refresh_shows()
        with patch('app.search_show',return_value=([],[])) as search:
            self.app.scan_selected();self.pump(lambda:'scan' not in self.app.jobs)
        self.assertEqual(search.call_count,1);self.assertEqual(search.call_args.args[0]['英文名'],'A')
        data=json.loads(path.read_text(encoding='utf-8'));self.assertEqual(len(data),2)
        self.assertEqual(next(r for r in data if r['show_id']==b)['count'],7)

    def test_simple_main_surface_keeps_help_and_diagnostics_in_secondary_screens(self):
        self.assertIsNotNone(button(self.root,'检查所选'));self.assertIsNotNone(button(self.root,'全部候选'))
        self.assertIsNone(button(self.root,'搜索诊断'));self.assertIsNone(button(self.root,'应用范围'))
        self.assertFalse(self.app.log.winfo_manager())
        self.app.library_tools.health();win=self.app.library_tools.windows[('health',None)]
        self.assertEqual(len(win.health_tree.get_children()),4);self.assertIsNotNone(button(win,'详细诊断'))

    def test_slow_show_does_not_block_results_of_other_show(self):
        from test_core import release
        import json
        a=self.app.store.save_show('Slow',{'英文名':'Slow'})
        b=self.app.store.save_show('Fast',{'英文名':'Fast'})
        gate=threading.Event(); self.gates.append(gate)
        def search(aliases,*args,**kwargs):
            if aliases['英文名']=='Slow': gate.wait(5)
            item=dict(release(1 if aliases['英文名']=='Slow' else 2),title=aliases['英文名']+' - 01')
            kwargs['on_progress']([item])
            return [item],[]
        with patch('app.search_show',side_effect=search):
            self.app.scan()
            self.pump(lambda:bool(self.app.store.candidates(b)))
            self.assertIn('scan',self.app.jobs);self.assertEqual(self.app.store.candidates(a),[])
            gate.set();self.pump(lambda:'scan' not in self.app.jobs)
        self.assertEqual(len(self.app.store.candidates(a)),1)
        self.assertEqual(len(json.loads((self.app.store.root/'last-search.json').read_text(encoding='utf-8'))),2)

    def setUp(self):
        # Dispose earlier Tcl interpreters on the UI thread before any worker can trigger GC.
        import gc
        gc.collect()
        self.tmp=tempfile.TemporaryDirectory()
        self.root=tk.Tk(); self.root.withdraw()
        self.app=App(self.root,self.tmp.name,prefetch=False); self.app.auto.set(False)
        self.gates=[]

    def tearDown(self):
        for gate in self.gates: gate.set()
        self.app.cancelled.set()
        for worker in self.app.workers.values(): worker.shutdown(wait=True,cancel_futures=True)
        for timer in self.root.tk.call('after','info'): self.root.after_cancel(timer)
        self.root.update_idletasks()
        self.app.close(); self.app.store.db.close(); self.tmp.cleanup()

    def pump(self, predicate, timeout=4):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:
            self.root.update(); time.sleep(.01)
        self.assertTrue(predicate())

    def editor(self):
        self.app.add(); self.root.update()
        return next(w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel))

    def entries(self,win):
        return [w for w in win.winfo_children() if isinstance(w,ttk.Entry)]

    def test_add_and_lookup_while_scan_busy(self):
        gate=threading.Event(); self.gates.append(gate)
        self.app.background(lambda:gate.wait(10),lambda _:None,key='scan')
        win=self.editor(); self.entries(win)[0].insert(0,'第一部')
        with patch('app.lookup',return_value=[{'中文名':'第一部','日文名':'作品一','id':1}]) as lookup:
            button(win,'查找并补全名称').invoke()
            self.pump(lambda:any(isinstance(w,tk.Toplevel) for w in win.winfo_children()))
            self.assertEqual(lookup.call_count,1)
            self.assertIn('scan',self.app.jobs)
        button(win,'保存并继续添加').invoke()
        self.assertTrue(win.winfo_exists())
        self.assertEqual(self.entries(win)[0].get(),'')
        self.entries(win)[0].insert(0,'第二部'); button(win,'保存并继续添加').invoke()
        self.assertEqual([r[1] for r in self.app.store.shows()],['第一部','第二部'])
        self.assertIn('scan',self.app.jobs)

    def test_old_completion_cannot_fill_next_show(self):
        gate=threading.Event(); self.gates.append(gate)
        win=self.editor(); self.entries(win)[0].insert(0,'第一部')
        def aliases(_): gate.wait(10); return 'Old Roman','Old English'
        with patch('app.lookup',return_value=[{'中文名':'第一部','日文名':'作品一','id':1}]),patch('app.complete_aliases',side_effect=aliases):
            button(win,'查找并补全名称').invoke()
            self.pump(lambda:any(isinstance(w,tk.Toplevel) for w in win.winfo_children()))
            picker=next(w for w in win.winfo_children() if isinstance(w,tk.Toplevel))
            box=next(w for w in picker.winfo_children() if isinstance(w,tk.Listbox)); box.selection_set(0)
            button(picker,'选用并补全罗马音/英文').invoke()
            button(win,'保存并继续添加').invoke()
            self.entries(win)[0].insert(0,'第二部'); gate.set()
            self.pump(lambda:not self.app.jobs)
            self.assertEqual(self.entries(win)[0].get(),'第二部')
            self.assertEqual(self.entries(win)[3].get(),'')
            self.assertEqual(self.entries(win)[4].get(),'')
            import json
            saved=json.loads(self.app.store.shows()[0][2])
            self.assertEqual(saved['罗马音'],'Old Roman')
            self.assertEqual(saved['英文名'],'Old English')

    def test_episode_filter_is_independent_per_show(self):
        from test_core import release
        a=self.app.store.save_show('第一部',{'中文名':'第一部','英文名':'Test'})
        b=self.app.store.save_show('第二部',{'中文名':'第二部','英文名':'Test'})
        for sid in [a,b]: self.app.store.ingest(sid,[release(i,str(i)) for i in range(1,6)])
        self.app.refresh_shows()
        self.app.first_ep.set('3'); self.app.last_ep.set('4'); self.app.apply_range()
        self.assertEqual(len(self.app.tree.get_children()),2)
        self.app.shows.selection_clear(0,'end'); self.app.shows.selection_set(1); self.app.refresh_candidates()
        self.assertEqual(len(self.app.tree.get_children()),5)
        self.assertEqual(self.app.first_ep.get(),'1')

    def test_monitor_completes_task_and_updates_visible_candidates(self):
        from test_core import release
        sid=self.app.store.save_show('测试',{'中文名':'测试','英文名':'Test'})
        self.app.store.ingest(sid,[release(i,str(i)) for i in range(1,7)])
        self.app.store.db.execute("UPDATE releases SET status='已转交' WHERE episode='5'"); self.app.store.db.commit()
        self.app.store.set_setting('task_xml','test.xml'); self.app.refresh_shows()
        with patch('core.completed_hashes',return_value={release(5)['hash']}),patch('core.task_records',return_value={release(5)['hash']:{'SaveDirectory':'test-output'}}):
            self.app.store.db.execute("UPDATE releases SET output='test-output' WHERE episode='5'"); self.app.store.db.commit()
            self.app.monitor_progress(); self.pump(lambda:'monitor' not in self.app.jobs)
        self.assertEqual(self.app.first_ep.get(),'4')
        self.assertEqual(len(self.app.tree.get_children()),2)

    def test_delete_button_during_scan(self):
        from test_core import release
        sid=self.app.store.save_show('测试',{'英文名':'Test'})
        self.app.store.ingest(sid,[release(1)]); self.app.refresh_shows()
        gate=threading.Event(); self.gates.append(gate)
        self.app.background(lambda:gate.wait(10),lambda _:self.app.store.ingest(sid,[release(2)]),key='scan')
        with patch('app.messagebox.askyesno',return_value=True): self.app.delete_selected()
        self.assertEqual(self.app.store.shows(),[]); self.assertEqual(self.app.tree.get_children(),())
        gate.set(); self.pump(lambda:'scan' not in self.app.jobs)
        self.assertEqual(self.app.store.db.execute('SELECT COUNT(*) FROM releases').fetchone()[0],0)

    def test_introduction_is_lazy_and_cache_is_reused(self):
        import json
        from test_details import subject_data,png
        from unittest.mock import Mock
        fetcher=Mock(side_effect=[json.dumps(subject_data()).encode(),png()]); self.app.detail_cache.fetcher=fetcher
        sid=self.app.store.save_show('测试动画',{'中文名':'测试动画','_bgm_id':'42'})
        self.app.refresh_shows(); self.root.update()
        self.app.refresh_candidates(); fetcher.assert_not_called()
        self.assertIsNone(self.app.detail_sid)
        self.app.open_detail(); self.pump(lambda:'detail:'+str(sid) not in self.app.jobs)
        self.assertEqual(self.app.detail_score.get(),'8.2'); self.assertIsNotNone(self.app.cover_photo)
        self.app.open_detail(); self.pump(lambda:'detail:'+str(sid) not in self.app.jobs)
        self.assertEqual(fetcher.call_count,2)

    def test_old_detail_response_does_not_replace_current_show(self):
        from test_details import subject_data
        gate=threading.Event(); self.gates.append(gate)
        a=self.app.store.save_show('第一部',{'_bgm_id':'42'}); b=self.app.store.save_show('第二部',{'_bgm_id':'43'})
        self.app.refresh_shows()
        def get(subject,force=False):
            if str(subject)=='42': gate.wait(10)
            return dict(subject_data(int(subject)),score=8.2,votes=100,rank=123,cached_at=time.time(),summary='简介')
        with patch.object(self.app.detail_cache,'get',side_effect=get):
            self.app.open_detail()
            self.app.shows.selection_clear(0,'end'); self.app.shows.selection_set(1); self.app.open_detail()
            self.pump(lambda:'detail:'+str(b) not in self.app.jobs)
            gate.set(); self.pump(lambda:not any(x.startswith('detail:') for x in self.app.jobs))
            self.assertEqual(self.app.detail_data['id'],43)

    def test_daily_prefetch_and_newly_added_show(self):
        import json
        from test_details import subject_data,png
        from unittest.mock import Mock
        def response(url,**_):
            return json.dumps(subject_data(int(url.rsplit('/',1)[-1]))).encode() if '/v0/' in url else png()
        fetcher=Mock(side_effect=response); self.app.detail_cache.fetcher=fetcher
        self.app.store.save_show('第一部',{'_bgm_id':'42'}); self.app.store.save_show('第二部',{'_bgm_id':'43'})
        self.app.refresh_shows(); self.app.prefetch_details=True; self.app.next_detail_batch=time.monotonic()-1
        self.pump(lambda:fetcher.call_count==4 and 'detail:batch' not in self.app.jobs)
        self.assertIsNone(self.app.detail_sid)
        self.app.refresh_library_details(); self.assertNotIn('detail:batch',self.app.jobs)
        self.app.store.save_show('第三部',{'_bgm_id':'44'}); self.app.refresh_shows(); self.app.refresh_library_details()
        self.pump(lambda:'detail:batch' not in self.app.jobs)
        self.assertEqual(fetcher.call_count,6)

    def test_prefetch_failure_is_throttled_and_keeps_cache(self):
        self.app.store.save_show('第一部',{'_bgm_id':'42'}); self.app.refresh_shows()
        with patch.object(self.app.detail_cache,'get',side_effect=TimeoutError('offline')) as get:
            self.app.refresh_library_details(); self.pump(lambda:'detail:batch' not in self.app.jobs)
            self.app.refresh_library_details(); self.assertEqual(get.call_count,1)

    def test_changed_binding_during_prefetch_is_not_marked_fresh(self):
        from test_details import subject_data
        gate=threading.Event(); self.gates.append(gate)
        sid=self.app.store.save_show('第一部',{'_bgm_id':'42'}); self.app.refresh_shows()
        def get(subject,**_):
            if str(subject)=='42': gate.wait(10)
            return dict(subject_data(int(subject)),cached_at=time.time())
        with patch.object(self.app.detail_cache,'get',side_effect=get) as load:
            self.app.refresh_library_details()
            self.app.store.save_show('第一部',{'_bgm_id':'43'},sid)
            gate.set(); self.pump(lambda:'detail:batch' not in self.app.jobs)
            self.assertNotIn(sid,self.app.detail_checked)
            self.app.refresh_library_details(); self.pump(lambda:'detail:batch' not in self.app.jobs)
            self.assertEqual([str(call.args[0]) for call in load.call_args_list],['42','43'])

    def test_downloader_settings_tabs_masked_password_and_saved_selection(self):
        self.app.settings(); self.root.update()
        win=next(w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel))
        tabs=next(w for w in win.winfo_children() if isinstance(w,ttk.Notebook))
        self.assertEqual([tabs.tab(t,'text') for t in tabs.tabs()],['常规','BitComet','qBittorrent','aria2','Motrix','迅雷'])
        general=tabs.nametowidget(tabs.tabs()[0]); page=tabs.nametowidget(tabs.tabs()[2])
        selector=next(w for w in general.winfo_children() if isinstance(w,ttk.Combobox)); selector.set('qBittorrent')
        entries=[w for w in page.winfo_children() if isinstance(w,ttk.Entry)]
        entries[1].insert(0,'test-user'); entries[2].insert(0,'ui-private-password')
        self.assertTrue(entries[2].cget('show'))
        button(win,'保存设置').invoke()
        self.assertEqual(self.app.store.setting('downloader'),'qbittorrent')
        self.assertEqual(self.app.downloader.get(),'qBittorrent')
        self.assertEqual(self.app.store.downloader_profile()['password'],'ui-private-password')
        self.assertNotIn('ui-private-password',self.app.store.setting('downloader_profile_qbittorrent'))

    def test_multi_select_delete_and_cancel(self):
        ids=[self.app.store.save_show('Test '+str(i),{'英文名':'Test'}) for i in range(3)]
        self.app.refresh_shows(); self.app.shows.selection_set(0,1)
        with patch('app.messagebox.askyesno',return_value=False): self.app.delete_selected()
        self.assertEqual(len(self.app.store.shows()),3)
        with patch('app.messagebox.askyesno',return_value=True): self.app.delete_selected()
        self.assertEqual([r[0] for r in self.app.store.shows()],[ids[2]])

    def test_multi_select_ignore_and_clear_hidden_unknowns(self):
        from test_core import release
        sid=self.app.store.save_show('Test',{'英文名':'Test'})
        items=[release(i,str(i)) for i in range(1,4)]
        unknown=dict(release(9),episode='待核对:Test special',title='Test special')
        self.app.store.ingest(sid,items+[unknown]); self.app.refresh_shows()
        self.app.tree.selection_set([r['hash'] for r in items[:2]])
        self.app.ignore()
        self.assertEqual({x['hash'] for x in self.app.store.candidates(sid)},{items[2]['hash'],unknown['hash']})
        self.app.show_unknown.set(False); self.app.refresh_candidates(); self.app.clear_unknown()
        self.app.show_unknown.set(True); self.app.refresh_candidates()
        self.assertEqual(self.app.tree.get_children(),(items[2]['hash'],))

    def test_theme_switch_preserves_selection_jobs_and_uses_cached_details(self):
        from appearance import THEMES
        from test_core import release
        sid=self.app.store.save_show('Test',{'英文名':'Test'}); self.app.store.ingest(sid,[release(1)]); self.app.refresh_shows()
        self.app.tree.selection_set(release(1)['hash'])
        before=set(self.app.jobs)
        with patch.object(self.app.detail_cache,'get',side_effect=AssertionError('Theme must not fetch metadata')):
            for palette in list(THEMES.values())+list(THEMES.values()):
                self.app.theme_choice.set(palette['name']); self.app.change_theme(); self.root.update_idletasks()
                self.assertEqual(self.app.tree.selection(),(release(1)['hash'],))
                self.assertEqual(self.app.shows.curselection(),(0,)); self.assertEqual(self.app.jobs,before)
                self.assertEqual(self.app.shows.cget('bg'),palette['card'])
        self.assertEqual(self.app.store.setting('theme'),'night')
        self.assertEqual(len(self.root._soft_button_images),12)

if __name__=='__main__': unittest.main()
