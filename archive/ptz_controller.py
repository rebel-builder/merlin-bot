#!/usr/bin/env python3
"""Merlin PTZ Controller — web-based camera control + preset saver."""

import http.server
import json
import requests
from requests.auth import HTTPDigestAuth

from config import CAMERA_AUTH, CAMERA_PTZ_BASE

AUTH = CAMERA_AUTH
BASE = CAMERA_PTZ_BASE
PORT = 8080

HTML = """<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Merlin PTZ</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0a0e17; color: #e2e8f0; font-family: -apple-system, sans-serif;
         display: flex; flex-direction: column; align-items: center; padding: 20px; }
  h1 { color: #00d4ff; margin-bottom: 20px; font-size: 20px; }
  .pad { display: grid; grid-template-columns: 80px 80px 80px; gap: 8px; margin: 20px 0; }
  .pad button { width: 80px; height: 80px; border-radius: 12px; border: 2px solid #1a2233;
                background: #0d1117; color: #e2e8f0; font-size: 28px; cursor: pointer;
                touch-action: manipulation; }
  .pad button:active { background: #1a2233; border-color: #00d4ff; }
  .pad .center { background: #1a1a2e; border-color: #00d4ff; font-size: 14px; }
  .presets { display: flex; flex-wrap: wrap; gap: 8px; margin: 20px 0; }
  .presets button { padding: 12px 20px; border-radius: 8px; border: 1px solid #1a2233;
                    background: #0d1117; color: #e2e8f0; font-size: 14px; cursor: pointer; }
  .presets button:active { background: #1a2233; }
  .presets .save { border-color: #ff6b35; color: #ff6b35; }
  .status { color: #5a7a99; font-size: 12px; margin: 10px 0; }
  .speed { margin: 10px 0; }
  .speed label { color: #5a7a99; font-size: 12px; }
  .speed input { width: 200px; }
</style>
</head>
<body>
<h1>Merlin PTZ</h1>

<div class="speed">
  <label>Speed: <span id="spd-val">5</span></label><br>
  <input type="range" id="speed" min="1" max="8" value="5">
</div>

<div class="pad">
  <button onclick="move('LeftUp')">&#8598;</button>
  <button onclick="move('Up')">&#8593;</button>
  <button onclick="move('RightUp')">&#8599;</button>
  <button onclick="move('Left')">&#8592;</button>
  <button class="center" onclick="home()">HOME</button>
  <button onclick="move('Right')">&#8594;</button>
  <button onclick="move('LeftDown')">&#8601;</button>
  <button onclick="move('Down')">&#8595;</button>
  <button onclick="move('RightDown')">&#8600;</button>
</div>

<div class="presets">
  <button onclick="gotoPreset(1)">Home (1)</button>
  <button onclick="gotoPreset(2)">Nod (2)</button>
  <button onclick="gotoPreset(3)">Left (3)</button>
  <button onclick="gotoPreset(4)">Right (4)</button>
  <button onclick="gotoPreset(5)">Up (5)</button>
</div>
<div class="presets">
  <button class="save" onclick="savePreset(1)">Save 1</button>
  <button class="save" onclick="savePreset(2)">Save 2</button>
  <button class="save" onclick="savePreset(3)">Save 3</button>
  <button class="save" onclick="savePreset(4)">Save 4</button>
  <button class="save" onclick="savePreset(5)">Save 5</button>
</div>

<div class="status" id="status">Ready</div>

<script>
const spd = document.getElementById('speed');
const spdVal = document.getElementById('spd-val');
const status = document.getElementById('status');
spd.oninput = () => spdVal.textContent = spd.value;

let moveTimer = null;

async function cmd(action) {
  status.textContent = action;
  try {
    const r = await fetch('/ptz?' + action);
    const t = await r.text();
    status.textContent = t;
  } catch(e) { status.textContent = 'Error: ' + e; }
}

function move(dir) {
  const s = spd.value;
  cmd('action=start&channel=0&code=' + dir + '&arg1=0&arg2=' + s + '&arg3=0');
  clearTimeout(moveTimer);
  moveTimer = setTimeout(() => {
    cmd('action=stop&channel=0&code=' + dir + '&arg1=0&arg2=0&arg3=0');
  }, 200);
}

function home() { gotoPreset(1); }
function gotoPreset(n) { cmd('action=start&channel=0&code=GotoPreset&arg1=0&arg2=' + n + '&arg3=0'); }
function savePreset(n) { cmd('action=start&channel=0&code=SetPreset&arg1=0&arg2=' + n + '&arg3=0'); }
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/ptz?"):
            query = self.path[5:]
            try:
                r = requests.get(f"{BASE}?{query}", auth=AUTH, timeout=3)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(r.text.encode())
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(str(e).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode())

    def log_message(self, format, *args):
        pass  # quiet


if __name__ == "__main__":
    print(f"Merlin PTZ Controller: http://localhost:{PORT}")
    http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
