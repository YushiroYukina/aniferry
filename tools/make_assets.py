"""Regenerate the project's original vector-inspired logo and Windows icon."""
from pathlib import Path
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]


def main():
    assets=ROOT/'assets'; assets.mkdir(exist_ok=True)
    docs=ROOT/'docs'/'images'; docs.mkdir(parents=True,exist_ok=True)
    svg='''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="AniFerry">
<rect x="8" y="8" width="240" height="240" rx="58" fill="#287f72"/>
<path d="M126 47v104H59z" fill="#f9f5e8"/>
<path d="M140 62l58 89h-58z" fill="#bfe2d7"/>
<path d="M47 167h162l-29 34H80z" fill="#f9f5e8"/>
<path d="M47 213q20-12 40 0t40 0t40 0t40 0" fill="none" stroke="#bfe2d7" stroke-width="10" stroke-linecap="round"/>
<circle cx="204" cy="53" r="11" fill="#f0cf86"/>
</svg>'''
    (assets/'aniferry.svg').write_text(svg,encoding='utf-8')
    image=Image.new('RGBA',(1024,1024),(0,0,0,0)); draw=ImageDraw.Draw(image)
    def pts(values): return [(x*4,y*4) for x,y in values]
    draw.rounded_rectangle((32,32,992,992),radius=232,fill='#287f72')
    draw.polygon(pts([(126,47),(126,151),(59,151)]),fill='#f9f5e8')
    draw.polygon(pts([(140,62),(198,151),(140,151)]),fill='#bfe2d7')
    draw.polygon(pts([(47,167),(209,167),(180,201),(80,201)]),fill='#f9f5e8')
    draw.ellipse((193*4,42*4,215*4,64*4),fill='#f0cf86')
    import math
    wave=[(x*4,int((213-6*math.sin((x-47)*math.pi/40))*4)) for x in range(47,208)]
    draw.line(wave,fill='#bfe2d7',width=32)
    image=image.resize((256,256),Image.Resampling.LANCZOS)
    image.save(assets/'aniferry.png'); image.save(docs/'logo.png')
    image.save(assets/'aniferry.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


if __name__=='__main__': main()
