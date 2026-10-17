"""Render public screenshots from fictional in-memory fixtures, never a user's library."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import App
from appearance import THEMES
from tools.render_native import capture_window


def settle(root):
    until=time.monotonic()+.2
    while time.monotonic()<until:
        root.update(); time.sleep(.01)


def main():
    docs=ROOT/'docs'/'images'; docs.mkdir(parents=True,exist_ok=True)
    build=ROOT/'build'/'public-demo'; build.mkdir(parents=True,exist_ok=True)
    font_path='C:/Windows/Fonts/msyh.ttc'
    with tempfile.TemporaryDirectory(prefix='aniferry-demo-') as temp:
        cover=Image.new('RGB',(300,430),'#234d58'); d=ImageDraw.Draw(cover)
        d.ellipse((150,-70,410,200),fill='#71978e'); d.ellipse((-140,260,210,610),fill='#316f72')
        d.polygon([(90,245),(205,245),(150,285)],fill='#f0cf86')
        d.polygon([(145,135),(145,230),(80,230)],fill='#e9f3ed')
        d.text((35,40),'星海来信',font=ImageFont.truetype(font_path,40),fill='#f5f8f2')
        d.text((37,98),'FICTIONAL DEMO',font=ImageFont.truetype(font_path,18),fill='#b6d5ca')
        d.text((40,355),'AniFerry',font=ImageFont.truetype(font_path,29),fill='#e6ede8')
        cover_path=Path(temp)/'demo-cover.png'; cover.save(cover_path)
        root=tk.Tk(); app=App(root,Path(temp)/'profile',prefetch=False)
        app.auto.set(False); app.next_progress=time.monotonic()+3600
        try:
            names=['星海来信','雨后车站','小岛的日常','云间旅人','月色便笺']
            for name in names: app.store.save_show(name,{'中文名':name})
            sid=app.store.shows()[0][0]
            items=[]
            for episode in range(1,7):
                mark='CHS' if episode%3 else 'RAW'
                items.append(dict(hash=f'{episode:040x}',title=f'[DemoSub] 星海来信 - {episode:02d} [1080p][{mark}]',
                    episode=str(episode),ani=False,source='演示源',seeds=120+episode*13,seed_known=True,
                    subtitle='标题声明中文' if mark=='CHS' else '无中文',size='325.0 MiB',
                    url='https://example.invalid/demo.torrent',page='https://example.invalid/demo'))
            app.store.ingest(sid,items); app.refresh_shows(); app.detail_sid=sid
            app.welcome.pack_forget(); app.intro.pack(fill='x',before=app.resource_section,pady=(0,14))
            app.render_detail(dict(id=42,name_cn=names[0],name='Letters from the Star Sea · 演示作品',
                summary='沿着潮汐送达的信件，把小岛上的日常与远方连接起来。\n这是一部仅用于界面演示的虚构动画，封面、简介、评分与候选均由示例数据生成。',
                platform='演示 TV',date='2026-10-01',eps=12,score=8.2,votes=1234,rank=128,
                distribution={str(i):i*17 for i in range(1,11)},cover_path=str(cover_path),cached_at=time.time()))
            app.refresh_candidates()
            for key,palette in THEMES.items():
                app.theme_choice.set(palette['name']); app.change_theme()
                app.detail_hint.set('演示数据 · 虚构作品与评分 · 不代表真实资源')
                app.note('演示画面：所有作品、评分与候选均为虚构数据')
                settle(root)
                app.detail_hint.set('演示数据 · 虚构作品与评分 · 不代表真实资源')
                root.update_idletasks(); capture_window(root,build/(key+'.png'))
            (docs/'overview.png').write_bytes((build/'sage.png').read_bytes())
            (docs/'dark.png').write_bytes((build/'night.png').read_bytes())
            sheet=Image.new('RGB',(1440,740),'#edf0f3'); draw=ImageDraw.Draw(sheet)
            font=ImageFont.truetype(font_path,20)
            for index,(key,palette) in enumerate(THEMES.items()):
                x=index%3*480; y=index//3*370
                draw.text((x+12,y+6),palette['name'],font=font,fill='#344258')
                with Image.open(build/(key+'.png')) as frame:
                    frame=frame.convert('RGB'); frame.thumbnail((468,326),Image.Resampling.LANCZOS)
                    sheet.paste(frame,(x+6,y+38))
            sheet.save(docs/'themes.png')
            if app.jobs: raise RuntimeError('Demo unexpectedly started a background operation')
        finally:
            for worker in app.workers.values(): worker.shutdown(wait=True,cancel_futures=True)
            app.close(); app.store.db.close()


if __name__=='__main__': main()
