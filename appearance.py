"""Local, resizable soft surfaces and ttk themes; no network assets required."""
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageFilter, ImageTk, ImageColor

THEMES = {
    'sage':dict(name='青岚',bg='#eef5f2',card='#fcfefd',ink='#263f3b',muted='#667f77',accent='#287f72',line='#dce9e2',soft='#e1f0e9'),
    'lilac':dict(name='暮紫',bg='#f2eff8',card='#fefcff',ink='#413854',muted='#7c718e',accent='#8063b2',line='#e7def0',soft='#ece4f7'),
    'sakura':dict(name='樱雾',bg='#faf0f3',card='#fffcfd',ink='#543a46',muted='#927181',accent='#b36083',line='#f0dde6',soft='#f7e3ec'),
    'cream':dict(name='暖杏',bg='#f8f2e8',card='#fffdf8',ink='#514536',muted='#8a7b64',accent='#9d713a',line='#ebe1ce',soft='#f3e7d2'),
    'mist':dict(name='晴空',bg='#eef3fa',card='#fcfdff',ink='#2e415b',muted='#70829b',accent='#4d77af',line='#dce5f1',soft='#e1ebf8'),
    'night':dict(name='深海',bg='#161e2e',card='#222d41',ink='#e4ebf7',muted='#a1b1ca',accent='#91b9ed',line='#33435f',soft='#303f58'),
}

def mix(a,b,weight):
    x=ImageColor.getrgb(a); y=ImageColor.getrgb(b)
    return '#'+''.join(f'{round(i*(1-weight)+j*weight):02x}' for i,j in zip(x,y))

def surface_image(width,height,palette,radius=22,shadow=True,fill=None):
    scale=2; w=max(2,width)*scale; h=max(2,height)*scale
    image=Image.new('RGBA',(w,h),(0,0,0,0))
    margin=6 if shadow else 1
    box=(margin*scale,2*scale,w-margin*scale-1,h-(10 if shadow else 2)*scale-1)
    if box[2]<=box[0] or box[3]<=box[1]: return image.resize((max(2,width),max(2,height)))
    mask=Image.new('L',(w,h)); ImageDraw.Draw(mask).rounded_rectangle(box,radius*scale,fill=255)
    if shadow:
        shade=Image.new('RGBA',(w,h),(0,0,0,0)); alpha=Image.new('L',(w,h))
        ImageDraw.Draw(alpha).rounded_rectangle((box[0],box[1]+5*scale,box[2],box[3]+5*scale),radius*scale,fill=32 if palette['bg']!='#161e2e' else 65)
        shade.putalpha(alpha.filter(ImageFilter.GaussianBlur(6*scale))); image.alpha_composite(shade)
    color=fill or palette['card']
    top=mix(color,'#ffffff',.035 if fill else (.012 if palette['bg']=='#161e2e' else .045)); bottom=mix(color,palette['accent'],.015)
    layer=Image.new('RGBA',(w,h)); brush=ImageDraw.Draw(layer)
    for y in range(h): brush.line((0,y,w,y),fill=mix(top,bottom,y/max(1,h-1)))
    layer.putalpha(mask); image.alpha_composite(layer)
    ImageDraw.Draw(image).rounded_rectangle(box,radius*scale,outline=mix(color,palette['line'],.65),width=scale)
    return image.resize((max(2,width),max(2,height)),Image.Resampling.LANCZOS)

class SoftCard(ttk.Frame):
    def __init__(self,parent,palette,padding=22,**kwargs):
        super().__init__(parent,style='TFrame',padding=padding,**kwargs)
        self.palette=palette; self._draw_job=None; self._image=None; self._size=None
        self._timer_root=self.winfo_toplevel()
        self.canvas=tk.Canvas(self,highlightthickness=0,borderwidth=0,bg=palette['bg'])
        self.canvas._surface_canvas=True
        self.canvas.place(x=0,y=0,relwidth=1,relheight=1,bordermode='ignore')
        self.tk.call('lower',str(self.canvas))
        self.bind('<Configure>',self._schedule)
        self.bind('<Destroy>',self._destroy,add='+')
    def _destroy(self,event):
        if event.widget==self and self._draw_job:
            self._timer_root.after_cancel(self._draw_job); self._draw_job=None
    def _schedule(self,event=None):
        if self._draw_job: self._timer_root.after_cancel(self._draw_job)
        self._draw_job=self._timer_root.after(45,self.redraw)
    def recolor(self,palette):
        self.palette=palette; self._size=None; self.canvas.configure(bg=palette['bg']); self._schedule()
    def redraw(self):
        self._draw_job=None
        size=(self.winfo_width(),self.winfo_height())
        if min(size)<12 or size==self._size: return
        self._size=size
        self._image=ImageTk.PhotoImage(surface_image(*size,self.palette),master=self)
        self.canvas.delete('all'); self.canvas.create_image(0,0,anchor='nw',image=self._image)


class SoftButton(ttk.Button):
    def __init__(self,parent,**kwargs):
        card=isinstance(parent,SoftCard) or ('style' in parent.keys() and 'Card' in str(parent.cget('style')))
        accent=kwargs.pop('style','')=='Accent.TButton'
        kwargs['style']=('Surface.' if card else 'Page.')+('Accent.TButton' if accent else 'TButton')
        super().__init__(parent,**kwargs)

def apply_style(root,key='sage'):
    palette=dict(THEMES.get(key,THEMES['sage']))
    bg,card,ink,muted,accent,line,soft=(palette[k] for k in ('bg','card','ink','muted','accent','line','soft'))
    root.configure(background=bg)
    style=ttk.Style(root)
    if style.theme_use()!='clam': style.theme_use('clam')
    style.configure('.',font=('Microsoft YaHei UI',10),background=bg,foreground=ink)
    for name in ('TFrame','TLabel','TCheckbutton','TNotebook','TPanedwindow'): style.configure(name,background=bg,foreground=ink)
    style.configure('Card.TFrame',background=card)
    style.configure('Card.TLabel',background=card)
    style.configure('Muted.TLabel',foreground=muted)
    style.configure('CardMuted.TLabel',background=card,foreground=muted,font=('Microsoft YaHei UI',9))
    style.configure('Title.TLabel',font=('Microsoft YaHei UI',21,'bold'))
    style.configure('Section.TLabel',background=card,font=('Microsoft YaHei UI',12,'bold'))
    # The lower 10px contain shadow; center the glyphs in the visible button body.
    style.configure('TButton',padding=(12,0,12,12),anchor='center',background=bg,borderwidth=0,foreground=ink)
    style.map('TButton',foreground=[('disabled',muted)],background=[('active',soft)])
    contrast=bg if key=='night' else '#ffffff'
    style.configure('Accent.TButton',foreground=contrast,background=bg)
    style.map('Accent.TButton',foreground=[('disabled',muted),('!disabled',contrast)])
    cache=getattr(root,'_soft_button_images',{}); root._soft_button_images=cache
    for kind in ('TButton','Accent.TButton'):
        element='soft.'+key+'.'+kind
        if element not in cache:
            fill=accent if kind.startswith('Accent') else card
            images=[ImageTk.PhotoImage(surface_image(70,42,palette,radius=12,shadow=True,fill=color),master=root)
                    for color in (fill,mix(fill,'#ffffff',.10),mix(fill,ink,.12),mix(fill,bg,.65))]
            cache[element]=images
            style.element_create(element,'image',images[0],('disabled',images[3]),('pressed',images[2]),('active',images[1]),border=(17,13,17,13),padding=0,sticky='nsew')
        style.layout(kind,[(element,{'sticky':'nsew','children':[('Button.focus',{'sticky':'nsew','children':[('Button.padding',{'sticky':'nsew','children':[('Button.label',{'sticky':'nsew'})]})]})]})])
    for prefix,color in [('Surface.',card),('Page.',bg)]:
        for kind in ('TButton','Accent.TButton'): style.configure(prefix+kind,background=color)
    style.configure('TEntry',padding=7,fieldbackground=card,foreground=ink,bordercolor=line,lightcolor=line,darkcolor=line,insertcolor=ink)
    style.map('TEntry',bordercolor=[('focus',accent)])
    style.configure('TCombobox',padding=6,fieldbackground=card,background=card,foreground=ink,arrowcolor=accent,bordercolor=line,lightcolor=line,darkcolor=line)
    style.map('TCombobox',fieldbackground=[('readonly',card)],foreground=[('readonly',ink)],selectbackground=[('readonly',soft)],selectforeground=[('readonly',ink)])
    style.configure('TNotebook',borderwidth=0,tabmargins=(0,4,0,0))
    style.configure('TNotebook.Tab',padding=(12,8),background=bg,foreground=muted)
    style.map('TNotebook.Tab',background=[('selected',card),('active',soft)],foreground=[('selected',accent)])
    style.configure('TCheckbutton',padding=5,background=card,indicatorbackground=card,indicatorforeground=accent)
    style.map('TCheckbutton',background=[('active',soft)],indicatorbackground=[('selected',accent)])
    style.configure('Treeview',background=card,fieldbackground=card,foreground=ink,rowheight=35,borderwidth=0,bordercolor=line,lightcolor=line,darkcolor=line,relief='flat')
    style.configure('Treeview.Heading',background=soft,foreground=muted,padding=(1,10),font=('Microsoft YaHei UI',9,'bold'),relief='flat')
    style.map('Treeview',background=[('selected',soft)],foreground=[('selected',ink)])
    for orient in ('Vertical','Horizontal'):
        style.configure(orient+'.TScrollbar',background=line,troughcolor=card,borderwidth=0,bordercolor=line,lightcolor=line,darkcolor=line,arrowsize=10,arrowcolor=muted)
    for option,value in [('Background',bg),('Listbox.background',card),('Listbox.foreground',ink),('Listbox.selectBackground',soft),('Listbox.selectForeground',ink),('Text.background',card),('Text.foreground',ink),('Text.insertBackground',ink)]:
        root.option_add('*'+option,value)
    return palette

def recolor_native(parent,palette):
    for widget in parent.winfo_children():
        if isinstance(widget,SoftCard): widget.recolor(palette)
        elif isinstance(widget,tk.Toplevel): widget.configure(bg=palette['bg'])
        elif isinstance(widget,tk.Listbox): widget.configure(bg=palette['card'],fg=palette['ink'],selectbackground=palette['soft'],selectforeground=palette['ink'])
        elif isinstance(widget,tk.Text):
            muted=getattr(widget,'_muted',False)
            widget.configure(bg=palette['bg' if muted else 'card'],fg=palette['muted' if muted else 'ink'],insertbackground=palette['ink'])
        elif isinstance(widget,tk.Label): widget.configure(bg=palette['soft'] if getattr(widget,'_cover',False) else palette['card'],fg=palette['accent'] if getattr(widget,'_score',False) else palette['muted'])
        elif isinstance(widget,tk.Canvas) and not getattr(widget,'_surface_canvas',False): widget.configure(bg=palette['card'])
        recolor_native(widget,palette)


def release_images(root):
    # PhotoImage finalizers must run on the Tk thread, including when workers still hold App.
    def clear(parent):
        for widget in parent.winfo_children():
            if isinstance(widget,SoftCard): widget._image=None
            clear(widget)
    clear(root)
    getattr(root,'_soft_button_images',{}).clear()
