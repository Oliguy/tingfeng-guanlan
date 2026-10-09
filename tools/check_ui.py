"""Repeatable browser release check. Never queries or writes market/user data.

By default uses installed HTTP assets; --source serves current source assets on
an ephemeral port. API requests are fulfilled as failures to check that global
navigation and settings remain usable when a module is unavailable.
"""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from guanlan_ui.assets import asset
from guanlan_ui import ENTRY_TITLES

MEASURE = """() => {
 const describe=selector=>{const el=document.querySelector(selector);if(!el)return null;
 const r=el.getBoundingClientRect(),s=getComputedStyle(el);
 return {x:r.x,y:r.y,width:r.width,height:r.height,fontFamily:s.fontFamily,
 fontSize:s.fontSize,lineHeight:s.lineHeight,padding:s.padding,zoom:s.zoom,transform:s.transform};};
 return {module:document.body.dataset.observerModule,width:innerWidth,height:innerHeight,
 scale:visualViewport.scale,rail:describe('.app-rail'),icon:describe('.app-brand img'),
 nav:describe('.app-rail [data-observer="industry30"]'),header:describe('.app-header'),
 footer:describe('.app-footer'),content:describe('.app-content'),settings:describe('#settingsPanel'),
 moduleContent:describe('#overview,.workspace,.movers-shell,.page'),
 pageOverflow:document.documentElement.scrollWidth>innerWidth,
 legacyRails:document.querySelectorAll('.home-rail,.rail').length};
}"""


class AssetsOnly(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        module = path.strip('/')
        try:
            payload, mime = asset('index.html', module=module) if module in ENTRY_TITLES else asset(module)
        except (FileNotFoundError, ValueError):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def check(base_url, output, widths):
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, exist_ok=True)
    samples, failures, errors = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport={'width':1366, 'height':768}, device_scale_factor=2)
        context.route('**/api/**', lambda route: route.fulfill(
            status=503, content_type='application/json', body='{"ok":false,"detail":"UI check: source unavailable"}'))
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        for width in widths:
            page.set_viewport_size({'width':width, 'height':768})
            page.goto(base_url + '/home/', wait_until='load')
            width_samples = []
            for module in ENTRY_TITLES:
                page.locator(f'.app-rail [data-observer="{module}"]').click()
                page.wait_for_url('**/' + module + '/')
                page.wait_for_function("() => document.querySelector('.observer-shell') && getComputedStyle(document.querySelector('.observer-shell')).display==='grid'")
                page.locator('#openSettings').click()
                page.locator('#settingsPanel').wait_for(state='visible')
                if samples and page.locator('#zoomGesture').input_value() != 'alt':
                    failures.append(f'{module}@{width}: cross-route preferences lost')
                sample = page.evaluate(MEASURE)
                samples.append(sample)
                width_samples.append(sample)
                expected_rail = 52 if width <= 760 else 64
                for field, expected in [('rail',expected_rail), ('header',56), ('footer',24)]:
                    actual = sample[field]['width' if field=='rail' else 'height']
                    if actual != expected:
                        failures.append(f'{module}@{width}: {field} {actual} != {expected}')
                if sample['moduleContent']['fontSize'] != '13px' or sample['scale'] != 1:
                    failures.append(f'{module}@{width}: base font or viewport scale changed')
                if sample['pageOverflow'] or sample['legacyRails']:
                    failures.append(f'{module}@{width}: page overflow or duplicate rail')
                settings = sample['settings']
                if settings['x'] < 0 or settings['x']+settings['width'] > width:
                    failures.append(f'{module}@{width}: settings outside viewport')
                # Preferences persist in the isolated browser context across routes/reload.
                page.locator('#zoomGesture').select_option('alt')
                page.keyboard.press('Escape')
                page.locator('#settingsPanel').wait_for(state='hidden')
                if width in (1366,430):
                    page.screenshot(path=str(output / f'{module}-{width}.png'))
            comparable = ['rail','icon','nav','header','footer','content','settings']
            first = {k:width_samples[0][k] for k in comparable}
            for sample in width_samples[1:]:
                if {k:sample[k] for k in comparable} != first:
                    failures.append(f'{sample["module"]}@{width}: shared geometry/font differs')
            page.reload(wait_until='load')
            page.locator('#openSettings').click()
            if page.locator('#zoomGesture').input_value() != 'alt':
                failures.append(f'reload@{width}: preferences lost')
            page.keyboard.press('Escape')
        browser.close()
    failures += ['pageerror: ' + e for e in errors]
    result = {'base_url':base_url, 'widths':widths, 'api_mode':'synthetic unavailable; no data reads or writes',
              'samples':samples, 'failures':failures, 'passed':not failures}
    (output / 'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':not failures,'samples':len(samples),'failures':failures,'output':str(output)},ensure_ascii=False))
    return 0 if not failures else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:18738')
    parser.add_argument('--source', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--widths', type=int, nargs='+', default=[1920,1366,1151,1150,821,820,761,760,430])
    args = parser.parse_args()
    if not args.source:
        return check(args.base_url.rstrip('/'), args.output, args.widths)
    server = ThreadingHTTPServer(('127.0.0.1',0), AssetsOnly)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        return check(f'http://127.0.0.1:{server.server_port}', args.output, args.widths)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
