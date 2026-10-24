# colab_proxy.py  -  uso: python colab_proxy.py vm1 [porta]
import asyncio, json, sys
from pathlib import Path
import aiohttp
from aiohttp import web, WSMsgType
from yarl import URL

STATE = Path.home() / ".config" / "colab-cli" / "sessions.json"
NAME = sys.argv[1] if len(sys.argv) > 1 else "vm0"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8899
SKIP = {"host", "origin", "content-length", "connection", "upgrade",
        "sec-websocket-key", "sec-websocket-version",
        "sec-websocket-extensions", "sec-websocket-protocol"}

def target():
    e = json.loads(STATE.read_text(encoding="utf-8"))[NAME]   # relido a cada request
    return e["url"].rstrip("/"), e["token"]

async def pump(src, dst):
    async for m in src:
        if m.type == WSMsgType.TEXT:
            await dst.send_str(m.data)
        elif m.type == WSMsgType.BINARY:
            await dst.send_bytes(m.data)
        else:
            break
    await dst.close()

async def handler(request: web.Request):
    base, token = target()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in SKIP}
    headers["X-Colab-Runtime-Proxy-Token"] = token

    async with aiohttp.ClientSession() as s:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            protos = [p.strip() for p in
                      request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
            url = str(URL(base + request.path_qs).update_query(
                {"colab-runtime-proxy-token": token}))
            print("WS pedido:", request.path, "protocolos:", protos, flush=True)
            try:
                ws_cli = await s.ws_connect(url, headers=headers, protocols=protos,
                                            max_msg_size=0)
            except Exception as ex:
                print("WS upstream FALHOU:", repr(ex), flush=True)
                return web.Response(status=502, text=f"upstream ws: {ex!r}")

            print("WS upstream OK, protocolo:", ws_cli.protocol, flush=True)
            ws_srv = web.WebSocketResponse(
                protocols=[ws_cli.protocol] if ws_cli.protocol else [],
                max_msg_size=0)
            await ws_srv.prepare(request)

            async def pump(src, dst, label):
                n = 0
                try:
                    async for m in src:
                        n += 1
                        if n <= 5:
                            print(f"WS {label} msg#{n} tipo={m.type.name}", flush=True)
                        if m.type == WSMsgType.TEXT:
                            await dst.send_str(m.data)
                        elif m.type == WSMsgType.BINARY:
                            await dst.send_bytes(m.data)
                        else:
                            break
                finally:
                    print(f"WS {label} encerrou ({n} msgs)", flush=True)
                    await dst.close()

            try:
                await asyncio.gather(pump(ws_srv, ws_cli, "vscode->colab"),
                                     pump(ws_cli, ws_srv, "colab->vscode"))
            finally:
                await ws_cli.close()
            return ws_srv
        """
        if request.headers.get("Upgrade", "").lower() == "websocket":
            protos = [p.strip() for p in
                      request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
            url = str(URL(base + request.path_qs).update_query(
                {"colab-runtime-proxy-token": token}))
            ws_srv = web.WebSocketResponse(protocols=protos)
            await ws_srv.prepare(request)
            async with s.ws_connect(url, headers=headers, protocols=protos) as ws_cli:
                await asyncio.gather(pump(ws_srv, ws_cli), pump(ws_cli, ws_srv))
            return ws_srv
        """
        body = await request.read()
        async with s.request(request.method, base + request.path_qs, headers=headers,
                             data=body, allow_redirects=False) as r:
            data = await r.read()
            out = {k: v for k, v in r.headers.items()
                   if k.lower() not in ("content-encoding", "transfer-encoding",
                                        "content-length", "connection")}
            print(request.method, request.path, "->", r.status, flush=True)
            return web.Response(status=r.status, body=data, headers=out)

app = web.Application()
app.router.add_route("*", "/{tail:.*}", handler)
web.run_app(app, host="127.0.0.1", port=PORT)
