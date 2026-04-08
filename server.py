import http.server
import socketserver
import json
import traceback

PORT = 8080
current_state = "{}"

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # ターミナルのログ出力を完全に無効化し、高頻度のポーリングでも重くならないようにする
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        if self.path == '/state':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(current_state.encode('utf-8'))
        else:
            super().do_GET()

    def do_POST(self):
        global current_state
        if self.path == '/state':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    post_data = self.rfile.read(content_length)
                    current_state = post_data.decode('utf-8')
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                print(f"Error handling POST: {e}")
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == '__main__':
    # 互換性が高く、多重リクエストを処理できるThreadingHTTPServerを使用
    class MyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        allow_reuse_address = True

    server_address = ('', PORT)
    try:
        httpd = MyServer(server_address, CustomHandler)
        print(f"Serving custom API + Static files at http://localhost:{PORT}")
        httpd.serve_forever()
    except Exception as e:
        print(f"Server initialization failed: {e}")
