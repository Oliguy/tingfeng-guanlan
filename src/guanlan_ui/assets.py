"""Shared workspace assets; no duplicate ETF page or hidden compatibility controls."""
from pathlib import Path
from . import ENTRY_TITLES
HERE = Path(__file__).parent
from functools import lru_cache
import re
LOCAL = frozenset(['format.js', 'member-strength.js', 'workspace-directory.js', 'workspace-signals.js', 'preferences.js', 'transport.js', 'workspace-model.js', 'workspace-view.js', 'workspace-updates.js', 'workspace-settings.js', 'workspace-themes.js', 'chart-core.js', 'chart-view.js', 'workspace.js', 'workspace.css', 'movers-model.js', 'movers-view.js', 'movers-leader-view.js', 'movers-workspace.js', 'movers.css', 'candle-inspector.js', 'candle-details.html', 'candle-details.js', 'candle-details.css', 'tingfeng-guanlan-icon-v2.ico'])
SCRIPT=re.compile(r'<script src="/([a-z-]+\.js)"></script>')
BUNDLES={'workspace-bundle.js':'workspace.html','movers-bundle.js':'movers.html','home-bundle.js':'home.html','training-bundle.js':'training.html'}
LOCAL=LOCAL|{'home.css','home.js','home-navigation.js','tingfeng-guanlan-icon-v2-64.png'}
LOCAL=LOCAL|{'training.css','training.js'}
LOCAL=LOCAL|{'shell.css','shell.js','connection.js'}

def shell(html,module,bundle):
    """Every route composes the same outer frame; existing controllers own content."""
    head=re.search(r'<head>(.*?)</head>',html,re.S).group(1)
    attrs=re.search(r'<body([^>]*)>',html).group(1)
    content=re.search(r'<body[^>]*>(.*?)</body>',html,re.S).group(1)
    # Page templates already contain only module content. Do not silently strip
    # duplicate navigation/settings: the release contract must reject it.
    content_attrs=' '.join(re.findall(r'data-[a-z-]+="[^"]*"',attrs))
    frame=(HERE/'static'/'app-shell.html').read_text('utf-8')
    frame=frame.replace('__HEAD__',head).replace('__BODY_ATTRS__',attrs).replace('__CONTENT__',SCRIPT.sub('',content))
    frame=frame.replace('__CONTENT_ATTRS__',content_attrs)
    frame=frame.replace('__TITLE__',ENTRY_TITLES[module]).replace('__FOOTER__',
        '日线竞价近似 · <span id="footer-status">涨停不能买 · 跌停不能卖 · T+1</span>' if module=='training' else '本地行情 · 点击更新才采集')
    frame=frame.replace('__SETTINGS_PANEL__',(HERE/'static'/'settings-panel.html').read_text('utf-8'))
    frame=frame.replace('data-observer="'+module+'"','data-observer="'+module+'" aria-current="page"')
    return frame.replace('__BUNDLE__',bundle)

@lru_cache(maxsize=8)
def _bundle(files):
    # Modules keep their own source files and order. A file edit invalidates the
    # transport bundle without introducing a build tool or a resident process.
    return b'\n;\n'.join((HERE/'static'/name).read_bytes() for name,mtime,size in files)

def asset(name, *, module='etf', page='list'):
    if name in ('','index.html'):
        if module not in ENTRY_TITLES: raise ValueError('Unknown observer')
        html=(HERE/'static'/('training.html' if module=='training' else 'home.html' if module=='home' else 'movers.html' if module=='movers' else 'workspace.html')).read_text('utf-8').replace('__MODULE__',module).replace('__TITLE__',ENTRY_TITLES[module]).replace('__MOVERS_PAGE__',page)
        for name in ('chart-panel','updates-panel'):
            html=html.replace('__'+name.upper().replace('-','_')+'__',(HERE/'static'/(name+'.html')).read_text('utf-8'))
        bundle='training-bundle.js' if module=='training' else 'home-bundle.js' if module=='home' else 'movers-bundle.js' if module=='movers' else 'workspace-bundle.js'
        # This single ordered bundle sits after all required DOM elements. Async
        # execution starts data reads while the render-blocking CSS downloads;
        # the chart's ResizeObserver redraws when the final layout is applied.
        html=shell(html,module,bundle)
        return html.encode(),'text/html; charset=utf-8'
    if name in BUNDLES:
        template=(HERE/'static'/BUNDLES[name]).read_text('utf-8')
        files=[]
        sources=SCRIPT.findall(template)
        sources=[s for s in sources if s!='preferences.js']
        for source in ['preferences.js','connection.js','shell.js',*sources]:
            if source not in LOCAL:raise FileNotFoundError(source)
            stat=(HERE/'static'/source).stat();files.append((source,stat.st_mtime_ns,stat.st_size))
        return _bundle(tuple(files)),'application/javascript; charset=utf-8'
    if name not in LOCAL: raise FileNotFoundError(name)
    kind = 'text/css' if name.endswith('.css') else 'text/html' if name.endswith('.html') else 'image/png' if name.endswith('.png') else 'image/x-icon' if name.endswith('.ico') else 'application/javascript'
    return (HERE/'static'/name).read_bytes(), kind+('' if name.endswith(('.ico','.png')) else '; charset=utf-8')
