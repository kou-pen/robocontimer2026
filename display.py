#!/usr/bin/env python3
"""
ROBOCON TIMER DISPLAY WINDOW  (Python ネイティブ版)
=====================================================
ブラウザ不要・OBS の「ウィンドウキャプチャ」で低遅延に取り込める表示ウィンドウ。
サーバーの SSE エンドポイントからリアルタイムに状態を受信して描画します。

使い方:
  python display.py              # フル表示 (プロジェクター / screen.html の代替)
  python display.py --overlay    # 上部バー表示 (OBS オーバーレイ / overlay.html の代替)
  python display.py --server http://192.168.x.x:8080   # 外部サーバー指定

操作:
  [F]          フルスクリーン切替
  [Escape]     フルスクリーン解除
  [Ctrl+Q]     終了
"""

import tkinter as tk
import tkinter.font as tkfont
import threading
import json
import urllib.request
import time
import sys
import struct
import math

# ───────────────────────────────────────────────────────────
#  コマンドライン引数
# ───────────────────────────────────────────────────────────
OVERLAY_MODE = '--overlay' in sys.argv
SERVER_URL   = "http://localhost:8080"
for i, a in enumerate(sys.argv):
    if a == '--server' and i + 1 < len(sys.argv):
        SERVER_URL = sys.argv[i + 1]

# ───────────────────────────────────────────────────────────
#  音声生成  ─  Web Audio API の sawtooth / square / triangle 波形を
#               numpy + winsound(WAV バッファ) で完全再現
# ───────────────────────────────────────────────────────────
try:
    import numpy as np
    import winsound
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False

_SAMPLE_RATE = 44100
_audio_lock  = threading.Lock()


def _gen_wave(freq: float, duration: float, vol: float,
              wave_type: str) -> bytes:
    """指定波形の PCM サンプル列を bytes で返す"""
    n = int(_SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, endpoint=False)

    if wave_type == 'sawtooth':
        s = 2.0 * (t * freq - np.floor(t * freq + 0.5))
    elif wave_type == 'square':
        s = np.sign(np.sin(2.0 * np.pi * freq * t))
    elif wave_type == 'triangle':
        s = 2.0 * np.abs(2.0 * (t * freq - np.floor(t * freq + 0.5))) - 1.0
    else:  # sine
        s = np.sin(2.0 * np.pi * freq * t)

    # 末尾だけ瞬時フェードアウト（ブツッ防止）
    fade = max(1, int(_SAMPLE_RATE * 0.01))
    s[-fade:] *= np.linspace(1.0, 0.0, fade)

    return (s * vol * 32767.0).clip(-32767, 32767).astype(np.int16).tobytes()


def _make_wav(pcm: bytes) -> bytes:
    """PCM bytes を WAV 形式に包む"""
    return struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + len(pcm), b'WAVE',
        b'fmt ', 16, 1, 1, _SAMPLE_RATE, _SAMPLE_RATE * 2, 2, 16,
        b'data', len(pcm)
    ) + pcm


def _play_raw(wav_bytes: bytes):
    if HAS_AUDIO:
        winsound.PlaySound(wav_bytes,
                           winsound.SND_MEMORY | winsound.SND_NODEFAULT)


def play_beep(freq: float, duration: float, vol: float = 0.8,
              wave_type: str = 'square', is_buzzer: bool = True):
    """
    js/audio.js の playBeep() と完全に同じ仕様で音を生成して再生する。
      is_buzzer=True  → sawtooth×2 + square の3オシレーターブザー
      is_buzzer=False → 単一の wave_type オシレーター
    """
    if not HAS_AUDIO:
        return

    def _run():
        if not _audio_lock.acquire(blocking=False):
            return   # 前の音がまだ鳴っているならスキップ
        try:
            if is_buzzer:
                # 3 オシレーター合計 (周波数をわずかにずらして濁らせる)
                p1 = _gen_wave(freq,     duration, vol / 3, 'sawtooth')
                p2 = _gen_wave(freq - 5, duration, vol / 3, 'square')
                p3 = _gen_wave(freq + 7, duration, vol / 3, 'sawtooth')
                pcm = (
                    np.frombuffer(p1, dtype=np.int16).astype(np.int32) +
                    np.frombuffer(p2, dtype=np.int16).astype(np.int32) +
                    np.frombuffer(p3, dtype=np.int16).astype(np.int32)
                ).clip(-32767, 32767).astype(np.int16).tobytes()
            else:
                pcm = _gen_wave(freq, duration, vol, wave_type)
            _play_raw(_make_wav(pcm))
        finally:
            _audio_lock.release()

    threading.Thread(target=_run, daemon=True).start()


def play_beep_short():
    """カウントダウン用 ピッ (500 Hz, triangle, 0.25 s)"""
    play_beep(500, 0.25, 1.0, 'triangle', False)


def play_beep_long():
    """スタート / 終了 ブザー (1000 Hz, 3 osc, 0.8 s)"""
    play_beep(1000, 0.8, 1.0, 'triangle', False)


# ───────────────────────────────────────────────────────────
#  カラー / デザイン定数  ─  style.css と完全一致
# ───────────────────────────────────────────────────────────
BG_DARK      = "#07090f"
TEXT_WHITE   = "#f0f4f8"
RED_TEAM     = "#e53935"
BLUE_TEAM    = "#1e88e5"
YELLOW_ALERT = "#fdd835"
PANEL_BG     = "#141928"   # rgba(20,25,40) の近似
GRAY_TEXT    = "#b0b8c4"

# フル表示 (screen.html) の下段チーム色
RED_SCORE_COLOR  = RED_TEAM
BLUE_SCORE_COLOR = BLUE_TEAM

# オーバーレイ背景 (body.overlay-mode { background: #00ff00 })
CHROMA_GREEN = "#00ff00"

# フォントが使えない場合の代替フォント (Windows)
FONT_TIME  = ("Consolas", "Courier New", "monospace")
FONT_LABEL = ("Segoe UI", "Arial", "sans-serif")


def best_font(families, size, bold=False):
    weight = "bold" if bold else "normal"
    available = tkfont.families()
    for f in families:
        if f in available:
            return (f, size, weight)
    return (families[-1], size, weight)


# ───────────────────────────────────────────────────────────
#  フル表示ウィンドウ  (screen.html と同等)
# ───────────────────────────────────────────────────────────
class FullDisplay:
    """
    screen.html の完全再現:
      上半分 = ラジアルグラデーション黒背景 + タイマー
      下半分 = 赤 / 青 左右パネル + スコア
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ROBOCON TIMER || Full Display")
        self.root.configure(bg=BG_DARK)
        self.root.geometry("1280x720")
        self.root.minsize(640, 360)

        self._prev_state:     dict  = {}
        self._last_state_str: str   = ""
        self._last_audio_key: str   = ""
        self._last_audio_t:   float = 0.0
        self._is_fullscreen:  bool  = False

        self.root.bind('<f>',         self._toggle_fs)
        self.root.bind('<F>',         self._toggle_fs)
        self.root.bind('<Escape>',    lambda e: self.root.attributes('-fullscreen', False))
        self.root.bind('<Control-q>', lambda e: self.root.destroy())
        self.root.bind('<Configure>', self._on_resize)

        self._build()
        self._start_sse()
        self._update_fonts()

    # ── UI 構築 ──────────────────────────────────────────────
    def _build(self):
        # ── 上半分: タイマー ────────────────────────────────
        self.f_top = tk.Frame(self.root, bg=PANEL_BG)
        self.f_top.place(relx=0, rely=0, relwidth=1, relheight=0.5)

        self.lbl_phase = tk.Label(
            self.f_top, text="SETTING TIME",
            bg=PANEL_BG, fg=GRAY_TEXT,
            font=best_font(FONT_LABEL, 20, bold=True)
        )
        self.lbl_phase.place(relx=0.5, rely=0.15, anchor='center')

        self.lbl_time = tk.Label(
            self.f_top, text="--:--",
            bg=PANEL_BG, fg=TEXT_WHITE,
            font=best_font(FONT_TIME, 110, bold=True)
        )
        self.lbl_time.place(relx=0.5, rely=0.62, anchor='center')

        self.lbl_conn = tk.Label(
            self.f_top, text="⚠  サーバーに接続中...",
            bg=PANEL_BG, fg="#e74c3c",
            font=best_font(FONT_LABEL, 11)
        )
        self.lbl_conn.place(relx=0.5, rely=0.93, anchor='center')

        # ── 下半分: スコアエリア ─────────────────────────────
        self.f_bottom = tk.Frame(self.root, bg=BG_DARK)
        self.f_bottom.place(relx=0, rely=0.5, relwidth=1, relheight=0.5)

        # 区切り線
        tk.Frame(self.root, bg="#2a2f45", height=3).place(
            relx=0, rely=0.497, relwidth=1, relheight=0
        )

        # 赤パネル (左)
        self.f_red = tk.Frame(self.f_bottom, bg=BG_DARK)
        self.f_red.place(relx=0, rely=0, relwidth=0.5, relheight=1)

        self.lbl_red_name = tk.Label(
            self.f_red, text="RED TEAM",
            bg=BG_DARK, fg=TEXT_WHITE,
            font=best_font(FONT_LABEL, 18, bold=True)
        )
        self.lbl_red_name.place(relx=0.5, rely=0.18, anchor='center')

        self.lbl_red_score = tk.Label(
            self.f_red, text="0",
            bg=BG_DARK, fg=RED_TEAM,
            font=best_font(FONT_TIME, 90, bold=True)
        )
        self.lbl_red_score.place(relx=0.5, rely=0.62, anchor='center')

        self.lbl_red_vgoal = tk.Label(
            self.f_red, text="V-GOAL!",
            bg=YELLOW_ALERT, fg=BG_DARK,
            font=best_font(FONT_TIME, 30, bold=True)
        )

        # 青パネル (右)
        self.f_blue = tk.Frame(self.f_bottom, bg=BG_DARK)
        self.f_blue.place(relx=0.5, rely=0, relwidth=0.5, relheight=1)

        self.lbl_blue_name = tk.Label(
            self.f_blue, text="BLUE TEAM",
            bg=BG_DARK, fg=TEXT_WHITE,
            font=best_font(FONT_LABEL, 18, bold=True)
        )
        self.lbl_blue_name.place(relx=0.5, rely=0.18, anchor='center')

        self.lbl_blue_score = tk.Label(
            self.f_blue, text="0",
            bg=BG_DARK, fg=BLUE_TEAM,
            font=best_font(FONT_TIME, 90, bold=True)
        )
        self.lbl_blue_score.place(relx=0.5, rely=0.62, anchor='center')

        self.lbl_blue_vgoal = tk.Label(
            self.f_blue, text="V-GOAL!",
            bg=YELLOW_ALERT, fg=BG_DARK,
            font=best_font(FONT_TIME, 30, bold=True)
        )

        # 中央の縦区切り線
        tk.Frame(self.f_bottom, bg="#2a2f45", width=2).place(
            relx=0.499, rely=0, relwidth=0, relheight=1
        )

    # ── フォントのリサイズ ────────────────────────────────────
    def _on_resize(self, event=None):
        self.root.after_idle(self._update_fonts)

    def _update_fonts(self):
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            if w < 10 or h < 10:
                return
            time_sz  = max(12, int(min(w * 0.23, h * 0.33)))
            score_sz = max(10, int(min(w * 0.13, h * 0.27)))
            phase_sz = max(10, int(min(w * 0.025, h * 0.048)))
            name_sz  = max(10, int(min(w * 0.022, h * 0.044)))
            vgoal_sz = max(10, int(min(w * 0.055, h * 0.10)))
            self.lbl_time.config(font=best_font(FONT_TIME,  time_sz,  bold=True))
            self.lbl_red_score.config(font=best_font(FONT_TIME,  score_sz, bold=True))
            self.lbl_blue_score.config(font=best_font(FONT_TIME, score_sz, bold=True))
            self.lbl_phase.config(font=best_font(FONT_LABEL, phase_sz, bold=True))
            self.lbl_red_name.config(font=best_font(FONT_LABEL, name_sz, bold=True))
            self.lbl_blue_name.config(font=best_font(FONT_LABEL, name_sz, bold=True))
            self.lbl_red_vgoal.config(font=best_font(FONT_TIME, vgoal_sz, bold=True))
            self.lbl_blue_vgoal.config(font=best_font(FONT_TIME, vgoal_sz, bold=True))
        except Exception:
            pass

    # ── 音声 dedup ────────────────────────────────────────────
    def _beep(self, key: str, is_long: bool):
        now = time.monotonic()
        if self._last_audio_key == key and now - self._last_audio_t < 0.5:
            return
        self._last_audio_key = key
        self._last_audio_t   = now
        if is_long:
            play_beep_long()
        else:
            play_beep_short()

    # ── 音声判定 ─────────────────────────────────────────────
    def _process_audio(self, state: dict):
        prev  = self._prev_state
        phase = state.get('phase', '')
        ttype = state.get('timerType', 'SETTING')
        tr    = state.get('timeRemaining', 0)

        if phase == 'PRE_START':
            txt = state.get('preStartText', '')
            if txt != prev.get('preStartText', ''):
                if txt in ('3', '2', '1'):
                    self._beep(f'pre_{txt}', False)
                elif txt == 'START':
                    self._beep('pre_START', True)

        if phase == 'RUNNING' and tr != prev.get('timeRemaining', -1):
            if ttype == 'SETTING':
                if tr in (3, 2, 1):
                    self._beep(f'set_{tr}', False)
            else:
                left = state.get('settings', {}).get('match', 180) - tr
                if left in (3, 2, 1):
                    self._beep(f'mat_{left}', False)

        if phase == 'END' and prev.get('phase') != 'END':
            self._beep('end', True)

    # ── UI 適用 ──────────────────────────────────────────────
    def _apply(self, state: dict):
        phase    = state.get('phase', 'IDLE')
        ttype    = state.get('timerType', 'SETTING')
        tr       = state.get('timeRemaining', 0)
        warning  = state.get('isWarning', False)
        pre_text = state.get('preStartText', '')
        red      = state.get('red', {})
        blue     = state.get('blue', {})

        # タイマー文字
        if phase == 'PRE_START':
            t_str = pre_text or 'READY'
        else:
            t_str = f"{tr // 60:02d}:{tr % 60:02d}"
        self.lbl_time.config(text=t_str)

        # フェーズラベル
        label = 'SETTING TIME' if ttype == 'SETTING' else 'MATCH TIME'
        sfx   = {'PRE_START': ' [COUNTDOWN]', 'PAUSED': ' [PAUSED]',
                  'END': ' [TIME UP]'}.get(phase, '')
        self.lbl_phase.config(text=label + sfx)

        # 警告カラー
        is_warn = phase == 'END' or warning
        self.lbl_time.config(fg=YELLOW_ALERT if is_warn else TEXT_WHITE)
        self.lbl_phase.config(fg=YELLOW_ALERT if is_warn else GRAY_TEXT)

        # スコア
        self.lbl_red_name.config(text=red.get('name', 'RED TEAM'))
        self.lbl_red_score.config(text=str(red.get('score', 0)))
        self.lbl_blue_name.config(text=blue.get('name', 'BLUE TEAM'))
        self.lbl_blue_score.config(text=str(blue.get('score', 0)))

        # V-GOAL
        def _vg(frame, name_l, score_l, vgoal_l, score_color, active, vt):
            if active:
                frame.config(bg=YELLOW_ALERT)
                name_l.config(bg=YELLOW_ALERT, fg=BG_DARK)
                score_l.config(bg=YELLOW_ALERT, fg=BG_DARK)
                if vt is not None:
                    vgoal_l.config(text=f"V-GOAL!\nTIME {vt//60:02d}:{vt%60:02d}")
                else:
                    vgoal_l.config(text="V-GOAL!")
                vgoal_l.place(relx=0.5, rely=0.9, anchor='center')
            else:
                frame.config(bg=BG_DARK)
                name_l.config(bg=BG_DARK, fg=TEXT_WHITE)
                score_l.config(bg=BG_DARK, fg=score_color)
                vgoal_l.place_forget()

        _vg(self.f_red,  self.lbl_red_name,  self.lbl_red_score,
            self.lbl_red_vgoal,  RED_TEAM,  red.get('vgoal',  False), red.get('vgoal_time'))
        _vg(self.f_blue, self.lbl_blue_name, self.lbl_blue_score,
            self.lbl_blue_vgoal, BLUE_TEAM, blue.get('vgoal', False), blue.get('vgoal_time'))

    # ── SSE コールバック ──────────────────────────────────────
    def on_state(self, state: dict):
        s = json.dumps(state, sort_keys=True)
        if s == self._last_state_str:
            return
        self._last_state_str = s
        self._process_audio(state)
        self._prev_state = state
        self.root.after(0, lambda st=state: self._apply(st))

    def set_connected(self, ok: bool):
        self.root.after(0, lambda: self.lbl_conn.config(
            text="" if ok else "⚠  サーバー未接続  (自動再接続中...)"
        ))

    # ── SSE 接続スレッド ──────────────────────────────────────
    def _start_sse(self):
        def _loop():
            while True:
                try:
                    req = urllib.request.Request(
                        f"{SERVER_URL}/events",
                        headers={'Accept': 'text/event-stream',
                                 'Cache-Control': 'no-cache'}
                    )
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        self.set_connected(True)
                        buf = ""
                        while True:
                            chunk = resp.read(1)
                            if not chunk:
                                break
                            buf += chunk.decode('utf-8', errors='replace')
                            if buf.endswith('\n\n'):
                                for line in buf.split('\n'):
                                    if line.startswith('data: ') and line[6:].strip():
                                        try:
                                            s = json.loads(line[6:])
                                            if s.get('timerType'):
                                                self.on_state(s)
                                        except Exception:
                                            pass
                                buf = ""
                except Exception:
                    self.set_connected(False)
                    time.sleep(2)
        threading.Thread(target=_loop, daemon=True).start()

    def _toggle_fs(self, event=None):
        self._is_fullscreen = not self._is_fullscreen
        self.root.attributes('-fullscreen', self._is_fullscreen)


# ───────────────────────────────────────────────────────────
#  オーバーレイウィンドウ  (overlay.html と同等)
#  上3分の1に「チームL | タイマー | チームR」横並びバー
# ───────────────────────────────────────────────────────────
class OverlayDisplay(FullDisplay):
    """
    overlay.html の完全再現:
      左: 赤チームボックス (dark bg + 赤ボーダー)
      中: タイマーボックス
      右: 青チームボックス
      背景: クロマキーグリーン (#00ff00)
    """
    DARK_BOX = "#0a0f19"

    def __init__(self, root: tk.Tk):
        # 親クラスの __init__ は呼ばない (別レイアウトを構築する)
        self.root = root
        self.root.title("ROBOCON TIMER || Overlay")
        self.root.configure(bg=CHROMA_GREEN)
        self.root.geometry("1280x200")
        self.root.minsize(800, 100)
        self.root.wm_attributes('-transparentcolor', CHROMA_GREEN)
        self.root.wm_attributes('-topmost', 1)

        self._prev_state:     dict  = {}
        self._last_state_str: str   = ""
        self._last_audio_key: str   = ""
        self._last_audio_t:   float = 0.0
        self._is_fullscreen:  bool  = False

        self.root.bind('<f>',         self._toggle_fs)
        self.root.bind('<F>',         self._toggle_fs)
        self.root.bind('<Escape>',    lambda e: self.root.attributes('-fullscreen', False))
        self.root.bind('<Control-q>', lambda e: self.root.destroy())
        self.root.bind('<Configure>', self._on_resize)

        self._build_overlay()
        self._start_sse()
        self._update_fonts_overlay()

    def _build_overlay(self):
        D = self.DARK_BOX

        # 外側コンテナ (背景クロマキー)
        self.f_outer = tk.Frame(self.root, bg=CHROMA_GREEN)
        self.f_outer.place(relx=0, rely=0, relwidth=1, relheight=1)

        # ── 赤チームボックス (左) ──
        self.f_red = tk.Frame(self.f_outer, bg=D,
                              highlightthickness=3, highlightbackground=RED_TEAM)
        self.f_red.place(relx=0.03, rely=0.05, relwidth=0.22, relheight=0.9)

        self.lbl_red_name = tk.Label(
            self.f_red, text="RED TEAM",
            bg=D, fg=TEXT_WHITE,
            font=best_font(FONT_LABEL, 14, bold=True)
        )
        self.lbl_red_name.pack(pady=(8, 0))

        self.lbl_red_score = tk.Label(
            self.f_red, text="0",
            bg=D, fg=RED_TEAM,
            font=best_font(FONT_TIME, 42, bold=True)
        )
        self.lbl_red_score.pack(expand=True)

        self.lbl_red_vgoal = tk.Label(
            self.f_red, text="V-GOAL!",
            bg=YELLOW_ALERT, fg=D,
            font=best_font(FONT_TIME, 14, bold=True)
        )

        # ── タイマーボックス (中) ──
        self.f_timer = tk.Frame(self.f_outer, bg=D,
                                highlightthickness=3, highlightbackground="#555555")
        self.f_timer.place(relx=0.28, rely=0.0, relwidth=0.44, relheight=1.0)

        self.lbl_phase = tk.Label(
            self.f_timer, text="SETTING TIME",
            bg=D, fg=GRAY_TEXT,
            font=best_font(FONT_LABEL, 12, bold=True)
        )
        self.lbl_phase.pack(pady=(6, 0))

        self.lbl_time = tk.Label(
            self.f_timer, text="--:--",
            bg=D, fg=TEXT_WHITE,
            font=best_font(FONT_TIME, 52, bold=True)
        )
        self.lbl_time.pack(expand=True)

        self.lbl_conn = tk.Label(
            self.f_timer, text="⚠ 未接続",
            bg=D, fg="#e74c3c",
            font=best_font(FONT_LABEL, 9)
        )
        self.lbl_conn.pack(pady=(0, 3))

        # ── 青チームボックス (右) ──
        self.f_blue = tk.Frame(self.f_outer, bg=D,
                               highlightthickness=3, highlightbackground=BLUE_TEAM)
        self.f_blue.place(relx=0.75, rely=0.05, relwidth=0.22, relheight=0.9)

        self.lbl_blue_name = tk.Label(
            self.f_blue, text="BLUE TEAM",
            bg=D, fg=TEXT_WHITE,
            font=best_font(FONT_LABEL, 14, bold=True)
        )
        self.lbl_blue_name.pack(pady=(8, 0))

        self.lbl_blue_score = tk.Label(
            self.f_blue, text="0",
            bg=D, fg=BLUE_TEAM,
            font=best_font(FONT_TIME, 42, bold=True)
        )
        self.lbl_blue_score.pack(expand=True)

        self.lbl_blue_vgoal = tk.Label(
            self.f_blue, text="V-GOAL!",
            bg=YELLOW_ALERT, fg=D,
            font=best_font(FONT_TIME, 14, bold=True)
        )

    def _update_fonts_overlay(self):
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            if w < 10 or h < 10:
                return
            time_sz  = max(10, int(h * 0.45))
            score_sz = max(10, int(h * 0.38))
            phase_sz = max(8,  int(h * 0.12))
            name_sz  = max(8,  int(h * 0.12))
            vgoal_sz = max(8,  int(h * 0.13))
            self.lbl_time.config(font=best_font(FONT_TIME,  time_sz,  bold=True))
            self.lbl_phase.config(font=best_font(FONT_LABEL, phase_sz, bold=True))
            self.lbl_red_name.config(font=best_font(FONT_LABEL, name_sz, bold=True))
            self.lbl_blue_name.config(font=best_font(FONT_LABEL, name_sz, bold=True))
            self.lbl_red_score.config(font=best_font(FONT_TIME, score_sz, bold=True))
            self.lbl_blue_score.config(font=best_font(FONT_TIME, score_sz, bold=True))
            self.lbl_red_vgoal.config(font=best_font(FONT_TIME, vgoal_sz, bold=True))
            self.lbl_blue_vgoal.config(font=best_font(FONT_TIME, vgoal_sz, bold=True))
        except Exception:
            pass

    def _on_resize(self, event=None):
        self.root.after_idle(self._update_fonts_overlay)

    # V-GOAL は overlay で pack/pack_forget を使う
    def _apply(self, state: dict):
        phase    = state.get('phase', 'IDLE')
        ttype    = state.get('timerType', 'SETTING')
        tr       = state.get('timeRemaining', 0)
        warning  = state.get('isWarning', False)
        pre_text = state.get('preStartText', '')
        red      = state.get('red', {})
        blue     = state.get('blue', {})
        D        = self.DARK_BOX

        # タイマー文字
        self.lbl_time.config(
            text=pre_text or 'READY' if phase == 'PRE_START'
            else f"{tr // 60:02d}:{tr % 60:02d}"
        )
        label = 'SETTING TIME' if ttype == 'SETTING' else 'MATCH TIME'
        sfx   = {'PRE_START': ' [COUNTDOWN]', 'PAUSED': ' [PAUSED]',
                  'END': '[TIME UP]'}.get(phase, '')
        self.lbl_phase.config(text=label + sfx)

        is_warn = phase == 'END' or warning
        self.lbl_time.config(fg=YELLOW_ALERT if is_warn else TEXT_WHITE)
        self.lbl_phase.config(fg=YELLOW_ALERT if is_warn else GRAY_TEXT)

        self.lbl_red_name.config(text=red.get('name', 'RED TEAM'))
        self.lbl_red_score.config(text=str(red.get('score', 0)))
        self.lbl_blue_name.config(text=blue.get('name', 'BLUE TEAM'))
        self.lbl_blue_score.config(text=str(blue.get('score', 0)))

        def _vg(frame, name_l, score_l, vgoal_l, score_color, active, vt):
            if active:
                frame.config(bg=YELLOW_ALERT, highlightbackground=BG_DARK)
                name_l.config(bg=YELLOW_ALERT, fg=BG_DARK)
                score_l.config(bg=YELLOW_ALERT, fg=BG_DARK)
                if vt is not None:
                    vgoal_l.config(text=f"V-GOAL!\nTIME {vt//60:02d}:{vt%60:02d}", justify='center')
                else:
                    vgoal_l.config(text="V-GOAL!")
                vgoal_l.config(bg=YELLOW_ALERT, fg=BG_DARK)
                vgoal_l.pack(side='bottom', pady=4)
            else:
                frame.config(bg=D)
                name_l.config(bg=D, fg=TEXT_WHITE)
                score_l.config(bg=D, fg=score_color)
                vgoal_l.pack_forget()

        _vg(self.f_red,  self.lbl_red_name,  self.lbl_red_score,
            self.lbl_red_vgoal,  RED_TEAM,  red.get('vgoal',  False), red.get('vgoal_time'))
        _vg(self.f_blue, self.lbl_blue_name, self.lbl_blue_score,
            self.lbl_blue_vgoal, BLUE_TEAM, blue.get('vgoal', False), blue.get('vgoal_time'))


# ───────────────────────────────────────────────────────────
#  エントリポイント
# ───────────────────────────────────────────────────────────
if __name__ == '__main__':
    mode = 'オーバーレイ (OBS 用)' if OVERLAY_MODE else 'フル表示 (プロジェクター用)'
    print("=" * 56)
    print("  ROBOCON TIMER DISPLAY  (Python ネイティブ版)")
    print("=" * 56)
    print(f"  モード    : {mode}")
    print(f"  サーバー  : {SERVER_URL}")
    print(f"  操作      : [F] フルスクリーン  /  [Ctrl+Q] 終了")
    if OVERLAY_MODE:
        print("  OBS設定   : ウィンドウキャプチャ後、")
        print("              フィルタ → クロマキー → 色 #00ff00")
    elif not HAS_AUDIO:
        print("  警告      : numpy / winsound が使えないため音声なし")
    print("=" * 56)

    root = tk.Tk()
    if OVERLAY_MODE:
        app = OverlayDisplay(root)
    else:
        app = FullDisplay(root)
    root.mainloop()
