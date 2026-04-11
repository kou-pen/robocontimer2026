import asyncio
import json
import os
import sys
import glob
import cv2
import numpy as np
from aiohttp import web, WSMsgType

# ─── 設定 ─────────────────────────────────────────────────────────
HTTPS_PORT  = 8443   # Android ブラウザ接続先 (getUserMedia に HTTPS 必須)
MJPEG_PORT  = 9001   # OBS 接続先 (HTTP - ローカルなので SSL 不要)
import socket

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

LOCAL_IP    = get_local_ip()

# 出力解像度 縦向き (portrait)
OUTPUT_W    = 720
OUTPUT_H    = 1280
JPEG_QUALITY = 82

# ─── フレーム配信ハブ ─────────────────────────────────────────────
# MJPEG クライアントごとに asyncio.Queue(maxsize=1) を持つ
mjpeg_clients: set[asyncio.Queue] = set()
preview_frame: np.ndarray | None = None   # OpenCV プレビュー用最新フレーム

def distribute_frame(img: np.ndarray) -> None:
    """全 MJPEG クライアントへ最新フレームを配信（古いフレームは破棄）"""
    global preview_frame
    preview_frame = img
    for q in list(mjpeg_clients):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(img)
        except asyncio.QueueFull:
            pass

def normalize_portrait(img: np.ndarray) -> np.ndarray:
    """横向きフレームは回転して縦向きに。その後 OUTPUT_W×OUTPUT_H へリサイズ"""
    h, w = img.shape[:2]
    if w > h:                                        # landscape → portrait
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        h, w = img.shape[:2]
    if w != OUTPUT_W or h != OUTPUT_H:
        img = cv2.resize(img, (OUTPUT_W, OUTPUT_H), interpolation=cv2.INTER_LINEAR)
    return img

# ─── WebSocket ハンドラ (Android → Python) ────────────────────────
async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    """Android のブラウザから JPEG バイナリを受け取る WebSocket エンドポイント"""
    ws = web.WebSocketResponse(max_msg_size=10 * 1024 * 1024)
    await ws.prepare(request)
    print(f"[WS] Client connected: {request.remote}")

    async for msg in ws:
        if msg.type == WSMsgType.BINARY:
            # JPEG バイト列 → numpy → BGR 画像
            nparr = np.frombuffer(msg.data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                continue

            img = normalize_portrait(img)

            # OpenCV プレビュー
            cv2.imshow("WebRTC Receiver", img)
            cv2.waitKey(1)

            # 全 MJPEG クライアントへ配信
            distribute_frame(img)

        elif msg.type == WSMsgType.ERROR:
            print(f"[WS] Error: {ws.exception()}")

    print(f"[WS] Client disconnected: {request.remote}")
    return ws

# ─── MJPEG ハンドラ (Python → OBS) ───────────────────────────────
async def mjpeg_handler(request: web.Request) -> web.StreamResponse:
    """OBS の「メディアソース」が読む MJPEG ストリーム"""
    response = web.StreamResponse()
    response.headers["Content-Type"] = "multipart/x-mixed-replace; boundary=frame"
    response.headers["Cache-Control"]    = "no-cache, no-store, must-revalidate"
    response.headers["X-Accel-Buffering"] = "no"      # nginx 等のバッファリング無効化
    response.headers["Access-Control-Allow-Origin"] = "*"
    await response.prepare(request)
    print(f"[MJPEG] Client connected: {request.remote}")

    q: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=1)
    mjpeg_clients.add(q)

    try:
        while True:
            try:
                img = await asyncio.wait_for(q.get(), timeout=5.0)
            except asyncio.TimeoutError:
                # タイムアウト時はキープアライブ代わりに空コメントを送る
                try:
                    await response.write(b"--frame\r\n\r\n")
                except Exception:
                    break
                continue

            ret, jpeg = cv2.imencode(
                ".jpg", img,
                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
            )
            if not ret:
                continue

            data = jpeg.tobytes()
            header = (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
            )
            try:
                await response.write(header + data + b"\r\n")
            except (ConnectionResetError, ConnectionAbortedError):
                break

    finally:
        mjpeg_clients.discard(q)
        print(f"[MJPEG] Client disconnected: {request.remote}")

    return response

# ─── その他ルート ─────────────────────────────────────────────────
async def index_handler(request: web.Request) -> web.Response:
    html = open(os.path.join(os.path.dirname(__file__), "index.html"), encoding="utf-8").read()
    return web.Response(content_type="text/html", text=html)

async def status_handler(request: web.Request) -> web.Response:
    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "mjpeg_clients": len(mjpeg_clients),
            "stream_url": f"http://{LOCAL_IP}:{MJPEG_PORT}/stream",
        })
    )

# ─── エントリーポイント ────────────────────────────────────────────
if __name__ == "__main__":
    import ssl

    # ── HTTPS アプリ (Android 接続用) ──────────────────────────────
    https_app = web.Application()
    https_app.router.add_get("/",       index_handler)
    https_app.router.add_get("/ws",     ws_handler)
    https_app.router.add_get("/status", status_handler)

    cert_file = os.path.join(os.path.dirname(__file__), "cert.pem")
    key_file  = os.path.join(os.path.dirname(__file__), "key.pem")

    ssl_context = None
    if os.path.exists(cert_file) and os.path.exists(key_file):
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    else:
        print("[Warning] cert.pem/key.pem が見つかりません。先に python generate_cert.py を実行してください。")

    # ── HTTP アプリ (OBS 接続用 MJPEG) ─────────────────────────────
    mjpeg_app = web.Application()
    mjpeg_app.router.add_get("/stream", mjpeg_handler)

    # ── 同一 asyncio ループで 2 サーバーを起動 ──────────────────────
    async def run_all():
        runner_https = web.AppRunner(https_app)
        runner_mjpeg = web.AppRunner(mjpeg_app)
        await runner_https.setup()
        await runner_mjpeg.setup()

        site_https = web.TCPSite(runner_https, "0.0.0.0", HTTPS_PORT, ssl_context=ssl_context)
        site_mjpeg = web.TCPSite(runner_mjpeg, "0.0.0.0", MJPEG_PORT)

        await site_https.start()
        await site_mjpeg.start()

        print()
        print("=" * 56)
        print(f"  📱 Android で開く : https://{LOCAL_IP}:{HTTPS_PORT}/")
        print(f"  📺 OBS に追加     : http://{LOCAL_IP}:{MJPEG_PORT}/stream")
        print(f"     ソース追加 → メディアソース → 上記URLを入力")
        print(f"     プロパティ > ネットワークキャッシュ(ms) = 0")
        print("=" * 56)
        print()

        # サーバーを無限稼働させる
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await runner_https.cleanup()
            await runner_mjpeg.cleanup()
            cv2.destroyAllWindows()

    asyncio.run(run_all())
