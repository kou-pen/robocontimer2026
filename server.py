import http.server
import socketserver
import json
import traceback
import threading
import time
import socket
import os

PORT = 8080

# SSEクライアントの管理
sse_clients = []
sse_lock = threading.Lock()

def broadcast_sse():
    """現在の状態をすべてのSSEクライアントにプッシュする"""
    state_json = json.dumps(state)
    message = f"data: {state_json}\n\n".encode('utf-8')
    dead_clients = []
    with sse_lock:
        for client in sse_clients:
            try:
                client.wfile.write(message)
                client.wfile.flush()
            except Exception:
                dead_clients.append(client)
        for dc in dead_clients:
            sse_clients.remove(dc)

# ----------------------------------------------------------------------------
# サーバー主導のステートマシン（遅延ゼロ対応のためPythonがタイマーの親玉となる）
# ----------------------------------------------------------------------------

state = {}
score_config = []
teams_list = []
_timer_thread = None
_timer_expected_next = 0.0

def load_configs():
    global score_config, teams_list
    try:
        with open('public/data/scores.json', 'r', encoding='utf-8') as f:
            score_config = json.load(f)
    except Exception:
        score_config = []
    try:
        with open('public/data/teams.json', 'r', encoding='utf-8') as f:
            teams_list = json.load(f)
    except Exception:
        teams_list = ["RED TEAM", "BLUE TEAM"]

def init_state():
    global state
    state = {
        'timerType': 'SETTING',
        'phase': 'IDLE',
        'timeRemaining': 60,
        'preStartText': '',
        'red': { 'name': teams_list[0] if len(teams_list) > 0 else 'RED TEAM', 'score': 0 },
        'blue': { 'name': teams_list[1] if len(teams_list) > 1 else 'BLUE TEAM', 'score': 0 },
        'settings': { 'setting': 60, 'match': 180 },
        'isWarning': False
    }
    
    for cfg in score_config:
        cid = cfg.get('id', '')
        if cfg.get('type') == 'number':
            state['red'][cid] = 0
            state['red'][cid + '_manual'] = 0
            state['red'][cid + '_auto'] = 0
            state['blue'][cid] = 0
            state['blue'][cid + '_manual'] = 0
            state['blue'][cid + '_auto'] = 0
        elif cfg.get('type') == 'toggle':
            state['red'][cid] = False
            state['blue'][cid] = False

def recalculate_scores_and_warning():
    """点数計算と警告フラグの更新"""
    # 1. Total Scores
    for team in ('red', 'blue'):
        total = 0
        for cfg in score_config:
            cid = cfg.get('id', '')
            ctype = cfg.get('type', '')
            pts = cfg.get('points', 0)
            
            p_manual = pts.get('manual', 0) if isinstance(pts, dict) else pts
            p_auto = pts.get('auto', 0) if isinstance(pts, dict) else pts
            
            if ctype == 'number':
                total += state[team].get(cid + '_manual', 0) * p_manual
                total += state[team].get(cid + '_auto', 0) * p_auto
                # 画面表示用に合計数を更新
                state[team][cid] = state[team].get(cid + '_manual', 0) + state[team].get(cid + '_auto', 0)
            elif ctype == 'toggle':
                # トグルはマニュアルポイントを利用
                if p_manual > 0 and state[team].get(cid, False):
                    total += p_manual
        state[team]['score'] = total

    # 2. Warning / Time Left (JSのロジックと同じ)
    state['isWarning'] = False
    if state['phase'] in ('RUNNING', 'END'):
        limit = state['settings']['match']
        time_left = state['timeRemaining'] if state['timerType'] == 'SETTING' else limit - state['timeRemaining']
        if time_left <= 3:
            state['isWarning'] = True
    elif state['phase'] == 'PRE_START':
        if state['preStartText'] in ('3', '2', '1'):
            state['isWarning'] = True

class TimerEngine:
    def __init__(self):
        self.running_thread = None
        self.stop_event = threading.Event()
        self._lock = threading.Lock()

    def start_tick(self):
        with self._lock:
            self.stop()
            self.stop_event.clear()
            self.running_thread = threading.Thread(target=self._run, daemon=True)
            self.running_thread.start()
            
    def stop(self):
        self.stop_event.set()

    def _run(self):
        # 毎秒ぴったりに実行するための自己補正ループ
        expected_next = time.time() + 1.0
        while not self.stop_event.is_set():
            now = time.time()
            sleep_duration = expected_next - now
            if sleep_duration > 0:
                # stop_event.waitを使えば即座にキャンセル可能
                if self.stop_event.wait(sleep_duration):
                    break
            expected_next += 1.0
            
            # Tick logic
            with sse_lock:
                changed = self._tick()
            if changed:
                broadcast_sse()

    def _tick(self):
        """1秒ごとの処理。状態が変化したら True を返す"""
        changed = False
        phase = state['phase']
        
        if phase == 'PRE_START':
            # 5 -> 4 -> 3 -> 2 -> 1 -> START -> RUNNING (2秒後)
            txt = state['preStartText']
            if txt == 'READY':
                state['preStartText'] = '5'
                changed = True
            elif txt in ('5', '4', '3', '2'):
                state['preStartText'] = str(int(txt) - 1)
                recalculate_scores_and_warning()
                changed = True
            elif txt == '1':
                state['preStartText'] = 'START'
                changed = True
            elif txt == 'START':
                # 1秒後 (STARTを1秒表示したあと、さらにRUNNINGに切り替える...JSでは2秒だったがPythonのTickではどうするか)
                # TICKは1秒置きなので、START -> wait -> RUNNING にする
                state['phase'] = 'RUNNING'
                # そのまま timeRemainingのカウントも進める
                changed = True

        if state['phase'] == 'RUNNING':
            # RUNNING中のタイマー進行
            if state['timerType'] == 'SETTING':
                if state['timeRemaining'] > 0:
                    state['timeRemaining'] -= 1
                    changed = True
                    if state['timeRemaining'] == 0:
                        state['phase'] = 'END'
                        self.stop_event.set() # Stop ticking
            else:
                # MATCH カウントアップ
                limit = state['settings']['match']
                if state['timeRemaining'] < limit:
                    state['timeRemaining'] += 1
                    changed = True
                    if state['timeRemaining'] == limit:
                        state['phase'] = 'END'
                        self.stop_event.set()

            if changed:
                recalculate_scores_and_warning()
                
        return changed

timer_engine = TimerEngine()

def handle_command(cmd_data: dict):
    cmd = cmd_data.get('cmd')
    with sse_lock:
        if cmd == 'start':
            if state['phase'] == 'RUNNING':
                pass
            elif state['phase'] == 'PAUSED':
                state['phase'] = 'RUNNING'
                timer_engine.start_tick()
            elif state['timerType'] == 'SETTING':
                if state['phase'] in ('IDLE', 'END'):
                    state['timeRemaining'] = state['settings']['setting']
                state['phase'] = 'RUNNING'
                recalculate_scores_and_warning()
                timer_engine.start_tick()
            else:
                # MATCH起動 (PRE_STARTシーケンスへ)
                state['phase'] = 'PRE_START'
                state['preStartText'] = 'READY'
                state['timeRemaining'] = 0
                recalculate_scores_and_warning()
                timer_engine.start_tick()

        elif cmd == 'pause':
            if state['phase'] in ('RUNNING', 'PRE_START'):
                timer_engine.stop()
                state['phase'] = 'PAUSED'
                state['preStartText'] = 'PAUSED'

        elif cmd == 'reset':
            timer_engine.stop()
            if state['timerType'] == 'SETTING':
                state['phase'] = 'IDLE'
                state['timeRemaining'] = state['settings']['setting']
            else:
                state['phase'] = 'PRE_START'
                state['preStartText'] = 'READY'
                state['timeRemaining'] = 0
            recalculate_scores_and_warning()

        elif cmd == 'switch_match':
            timer_engine.stop()
            if state['timerType'] == 'SETTING':
                state['timerType'] = 'MATCH'
                state['timeRemaining'] = 0
                state['phase'] = 'PRE_START'
                state['preStartText'] = 'READY'
            else:
                state['timerType'] = 'SETTING'
                state['timeRemaining'] = state['settings']['setting']
                state['phase'] = 'IDLE'
            recalculate_scores_and_warning()

        elif cmd == 'set_settings':
            s_min = int(cmd_data.get('setting_min', 0))
            s_sec = int(cmd_data.get('setting_sec', 0))
            m_min = int(cmd_data.get('match_min', 0))
            m_sec = int(cmd_data.get('match_sec', 0))
            state['settings']['setting'] = s_min * 60 + s_sec
            state['settings']['match'] = m_min * 60 + m_sec
            recalculate_scores_and_warning()

        elif cmd == 'set_name':
            team = cmd_data.get('team')
            if team in ('red', 'blue'):
                state[team]['name'] = cmd_data.get('name', '')

        elif cmd == 'score':
            team = cmd_data.get('team')
            cid = cmd_data.get('id')
            amt = cmd_data.get('amt')
            is_auto = cmd_data.get('isAuto', False)
            
            if team in ('red', 'blue') and cid:
                if amt == 'toggle':
                    is_on = not state[team].get(cid, False)
                    state[team][cid] = is_on
                    # V-GOAL有効時はPAUSEしてタイムを記録
                    if cid == 'vgoal':
                        if is_on:
                            state[team]['vgoal_time'] = state['timeRemaining']
                            if state['phase'] in ('RUNNING', 'PRE_START'):
                                timer_engine.stop()
                                state['phase'] = 'PAUSED'
                                state['preStartText'] = 'PAUSED'
                        else:
                            state[team]['vgoal_time'] = None
                else:
                    mode_key = cid + ('_auto' if is_auto else '_manual')
                    current_val = state[team].get(mode_key, 0)
                    if isinstance(current_val, int):
                        new_val = max(0, current_val + int(amt))
                        state[team][mode_key] = new_val
                        
            recalculate_scores_and_warning()
            
    broadcast_sse()

# ----------------------------------------------------------------------------
# HTTPリクエストハンドラ
# ----------------------------------------------------------------------------

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="public", **kwargs)

    def setup(self):
        super().setup()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        if self.path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Connection', 'keep-alive')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()

            # 初期状態を即座に送信
            try:
                self.wfile.write(f"data: {json.dumps(state)}\n\n".encode('utf-8'))
                self.wfile.flush()
            except Exception:
                return

            with sse_lock:
                sse_clients.append(self)

            try:
                while True:
                    time.sleep(1)
                    self.wfile.write(b': ping\n\n')
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                with sse_lock:
                    if self in sse_clients:
                        sse_clients.remove(self)

        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/command':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    post_data = self.rfile.read(content_length)
                    cmd_data = json.loads(post_data.decode('utf-8'))
                    # コマンドの処理とブロードキャスト
                    handle_command(cmd_data)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                print(f"Error handling POST: {e}")
                self.send_response(500)
                self.end_headers()
        elif self.path == '/state':
             # 後方互換性。使われない想定
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == '__main__':
    load_configs()
    init_state()
    recalculate_scores_and_warning()
    
    class MyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            import sys
            exc = sys.exc_info()[1] if hasattr(sys, 'exc_info') else None
            if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
                return
            super().handle_error(request, client_address)

    server_address = ('', PORT)
    try:
        httpd = MyServer(server_address, CustomHandler)
        
        import socket
        local_ip = "127.0.0.1"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
        except Exception:
            pass

        print(f"===========================================================")
        print(f"  ROBOCON TIMER SERVER is running!")
        print(f"===========================================================")
        print(f"  Serving custom API + Static files from public/")
        print(f"  -> Local access    : http://localhost:{PORT}")
        print(f"  -> Network access  : http://{local_ip}:{PORT}")
        print(f"")
        print(f"  Server-side timer engine is now ACTIVE.")
        print(f"===========================================================")
        httpd.serve_forever()
    except Exception as e:
        print(f"Server initialization failed: {e}")
