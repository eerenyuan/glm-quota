"""
GLM Coding Plan Quota Monitor
System tray icon + local web dashboard.
Dependencies: pystray, Pillow (auto-installed by start.bat if missing)
"""
import json, os, sys, threading, webbrowser, time, io
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen

DIR = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(DIR, 'config.json')
PORT = 9823
GLM_API = 'https://open.bigmodel.cn/api/monitor/usage/quota/limit'

# Shared state between server thread and tray thread
_state = {'quota': None, 'icon': None, 'running': True}


def load_cfg():
    if os.path.exists(CFG):
        with open(CFG, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cfg(d):
    with open(CFG, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def fetch_quota(api_key):
    req = Request(GLM_API, headers={
        'Authorization': api_key,
        'Content-Type': 'application/json',
    })
    with urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    if not data.get('success'):
        raise RuntimeError(data.get('msg', 'API error'))
    limits = data['data']['limits']
    level = data['data'].get('level', '?')
    token_lims = [l for l in limits if l['type'] == 'TOKENS_LIMIT']
    time_lims = [l for l in limits if l['type'] == 'TIME_LIMIT']
    return {
        'level': level,
        'hour5': {'pct': token_lims[0]['percentage'], 'reset': token_lims[0].get('nextResetTime')} if token_lims else None,
        'week': {'pct': token_lims[1]['percentage'], 'reset': token_lims[1].get('nextResetTime')} if len(token_lims) > 1 else None,
        'mcp': {
            'used': time_lims[0].get('currentValue', 0), 'total': time_lims[0].get('usage', 0),
            'left': time_lims[0].get('remaining', 0), 'pct': time_lims[0]['percentage'],
            'reset': time_lims[0].get('nextResetTime'), 'details': time_lims[0].get('usageDetails', []),
        } if time_lims else None,
        'ts': int(time.time() * 1000),
    }


# ---- Tray icon helpers ----
# Ultra-vivid, high-saturation colors for maximum visibility
def _color(pct):
    if pct >= 90: return (255, 0, 0)       # pure red
    if pct >= 70: return (255, 80, 0)      # vivid orange
    if pct >= 45: return (255, 200, 0)     # bright yellow
    return (0, 220, 80)                    # vivid green


def make_icon(pct=None):
    """Create a 64x64 RGBA icon: rounded square + maximum-size bold number."""
    from PIL import Image, ImageDraw, ImageFont
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if pct is not None:
        col = _color(pct)
        # Rounded rectangle filling the entire icon
        margin = 1
        radius = 10
        draw.rounded_rectangle(
            [margin, margin, size - margin - 1, size - margin - 1],
            radius=radius,
            fill=col + (255,),
        )
        # Maximum-size bold percentage number
        try:
            font = ImageFont.truetype('arialbd.ttf', 42)
        except Exception:
            try:
                font = ImageFont.truetype('arial.ttf', 42)
            except Exception:
                font = ImageFont.load_default()
        txt = f'{pct}'
        # Use textbbox for precise centering
        bb = draw.textbbox((0, 0), txt, font=font)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        cx, cy = size / 2, size / 2
        # Adaptive text color: dark for light backgrounds, white for dark
        luminance = 0.299 * col[0] + 0.587 * col[1] + 0.114 * col[2]
        text_color = (0, 0, 0, 255) if luminance > 160 else (255, 255, 255, 255)
        # Center text - account for bbox offset
        x = cx - tw / 2 - bb[0]
        y = cy - th / 2 - bb[1]
        draw.text((x, y), txt, fill=text_color, font=font)
    else:
        # Default: grey rounded square
        margin = 1
        radius = 10
        draw.rounded_rectangle(
            [margin, margin, size - margin - 1, size - margin - 1],
            radius=radius,
            fill=(100, 116, 139, 220),
        )

    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return img


def update_icon():
    """Refresh tray icon with current quota data."""
    icon = _state.get('icon')
    quota = _state.get('quota')
    if not icon:
        return
    # Use the most-used quota (weekly) for the tray display
    pct = None
    if quota:
        if quota.get('week'):
            pct = quota['week']['pct']
        elif quota.get('hour5'):
            pct = quota['hour5']['pct']
    try:
        icon.icon = make_icon(pct)
        if pct is not None:
            lvl = (quota.get('level') or '').upper()
            icon.tooltip = f'GLM {lvl} — Week {pct}% | 5h {quota.get("hour5", {}).get("pct", "?")}%'
    except Exception:
        pass


def refresh_loop(interval=30):
    """Background thread: periodically fetch quota and update tray icon + widget."""
    cfg = load_cfg()
    api_key = cfg.get('api_key', '')
    while _state['running']:
        if api_key:
            try:
                data = fetch_quota(api_key)
                _state['quota'] = data
                update_icon()
                update_widget()
            except Exception:
                pass
        time.sleep(interval)


def update_widget():
    """Update the floating widget window if it exists."""
    widget = _state.get('widget')
    quota = _state.get('quota')
    if not widget or not quota:
        return
    try:
        pct = None
        if quota.get('week'):
            pct = quota['week']['pct']
        elif quota.get('hour5'):
            pct = quota['hour5']['pct']
        if pct is not None:
            col = _color(pct)
            bg = f'#{col[0]:02x}{col[1]:02x}{col[2]:02x}'
            widget.configure(bg=bg)
            widget.label.configure(text=f'{pct}%', bg=bg)
            widget.title(f'GLM Week {pct}%')
    except Exception:
        pass


# ---- HTTP Server ----
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self._serve_file(os.path.join(DIR, 'index.html'), 'text/html; charset=utf-8')
        elif self.path == '/api/quota':
            self._serve_quota()
        elif self.path == '/api/config':
            self._serve_config()
        else:
            self._respond(404, {'error': 'not found'})

    def do_POST(self):
        if self.path == '/api/config':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            cfg = load_cfg()
            cfg.update(body)
            save_cfg(cfg)
            self._respond(200, {'ok': True})
        else:
            self._respond(404, {'error': 'not found'})

    def _serve_file(self, path, content_type):
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_config(self):
        cfg = load_cfg()
        masked = dict(cfg)
        if 'api_key' in masked and masked['api_key']:
            k = masked['api_key']
            masked['api_key_masked'] = k[:8] + '...' + k[-4:] if len(k) > 12 else '***'
            del masked['api_key']
        self._respond(200, masked)

    def _serve_quota(self):
        cfg = load_cfg()
        api_key = cfg.get('api_key', '')
        if not api_key:
            self._respond(400, {'error': 'No API key configured'})
            return
        try:
            data = fetch_quota(api_key)
            _state['quota'] = data
            update_icon()
            self._respond(200, data)
        except Exception as e:
            self._respond(502, {'error': str(e)})

    def _respond(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server():
    """Run HTTP server in background daemon thread."""
    server = HTTPServer(('127.0.0.1', PORT), Handler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ---- Floating Widget ----
def run_widget():
    """Small always-on-top borderless window showing quota percentage."""
    import tkinter as tk

    cfg = load_cfg()
    if not cfg.get('api_key'):
        print('No API key configured. Run without --widget first to set it up.')
        return

    # Start HTTP server
    start_server()
    url = f'http://127.0.0.1:{PORT}'

    # Create widget window
    root = tk.Tk()
    root.title('GLM Quota')
    root.overrideredirect(True)  # No title bar, no border
    root.attributes('-topmost', True)  # Always on top
    root.attributes('-alpha', 0.92)  # Slightly transparent

    # Small size, fits on taskbar area
    W, H = 72, 36
    root.geometry(f'{W}x{H}')

    # Position: bottom-right of screen, above taskbar
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    # Place near bottom-right, above taskbar (taskbar ~40px)
    x = screen_w - W - 20
    y = screen_h - H - 60
    root.geometry(f'+{x}+{y}')

    # Make it draggable
    def start_drag(event):
        root._drag_x = event.x
        root._drag_y = event.y

    def do_drag(event):
        new_x = root.winfo_x() + event.x - root._drag_x
        new_y = root.winfo_y() + event.y - root._drag_y
        root.geometry(f'+{new_x}+{new_y}')

    root.bind('<Button-1>', start_drag)
    root.bind('<B1-Motion>', do_drag)

    # Double-click to open dashboard
    def on_double_click(event):
        webbrowser.open(url)

    root.bind('<Double-Button-1>', on_double_click)

    # Right-click for context menu
    def on_right_click(event):
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label='Open Dashboard', command=lambda: webbrowser.open(url))
        menu.add_command(label='Refresh Now', command=lambda: _widget_refresh_once())
        menu.add_separator()
        menu.add_command(label='Quit', command=lambda: _quit_widget(root))
        menu.post(event.x_root, event.y_root)

    root.bind('<Button-3>', on_right_click)

    # Label for percentage
    label = tk.Label(root, text='--', font=('Arial', 18, 'bold'), fg='white', bg='#555555')
    label.pack(expand=True, fill='both')
    root.label = label

    # Store reference
    _state['widget'] = root

    # Start auto-refresh
    t = threading.Thread(target=refresh_loop, args=(30,), daemon=True)
    t.start()

    # Open browser after a short delay
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f'GLM Quota Widget running')
    print(f'Dashboard: {url}')
    print('Drag to move, double-click to open dashboard, right-click for menu.')

    root.mainloop()


def _widget_refresh_once():
    """Single refresh for widget mode."""
    api_key = load_cfg().get('api_key', '')
    if api_key:
        try:
            data = fetch_quota(api_key)
            _state['quota'] = data
            update_icon()
            update_widget()
        except Exception:
            pass


def _quit_widget(root):
    """Quit widget mode."""
    _state['running'] = False
    root.destroy()


# ---- Tray App ----
def run_tray():
    import pystray
    from pystray import MenuItem as Item

    cfg = load_cfg()
    if not cfg.get('api_key'):
        print('=== GLM Quota Monitor ===')
        print('Enter your GLM Coding Plan API key:')
        print('(Get it from https://bigmodel.cn/usercenter/proj-mgmt/apikeys)')
        key = input('> ').strip()
        if key:
            cfg['api_key'] = key
            save_cfg(cfg)
            print('Saved.')

    # Start HTTP server
    start_server()
    url = f'http://127.0.0.1:{PORT}'

    # Create tray icon
    icon = pystray.Icon('GLM Quota', make_icon(), 'GLM Quota Monitor')

    def on_open(icon, item):
        webbrowser.open(url)

    def on_refresh(icon, item):
        refresh_loop_once()

    def on_quit(icon, item):
        _state['running'] = False
        icon.stop()

    def refresh_loop_once():
        """Single refresh, called from menu."""
        api_key = load_cfg().get('api_key', '')
        if api_key:
            try:
                data = fetch_quota(api_key)
                _state['quota'] = data
                update_icon()
            except Exception:
                pass

    icon.menu = pystray.Menu(
        Item('Open Dashboard', on_open, default=True),
        Item('Refresh Now', on_refresh),
        pystray.Menu.SEPARATOR,
        Item('Quit', on_quit),
    )

    _state['icon'] = icon

    # Start auto-refresh thread
    t = threading.Thread(target=refresh_loop, args=(30,), daemon=True)
    t.start()

    # Open browser after a short delay
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f'GLM Quota Monitor running — tray icon active')
    print(f'Dashboard: {url}')
    print('Right-click the tray icon for options. Close the console window to quit.')

    icon.run()


# ---- Web-only mode (no tray) ----
def run_web_only():
    server = start_server()
    url = f'http://127.0.0.1:{PORT}'
    print(f'GLM Quota Monitor running at {url}')
    print('Press Ctrl+C to stop.')
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\nStopped.')
        server.server_close()


if __name__ == '__main__':
    if '--widget' in sys.argv:
        run_widget()
    elif '--web-only' in sys.argv:
        run_web_only()
    else:
        try:
            run_tray()
        except Exception as e:
            print(f'Tray mode failed ({e}), falling back to web-only...')
            run_web_only()
