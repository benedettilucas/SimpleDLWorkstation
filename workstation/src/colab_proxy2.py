# colab_proxy.py
#   python colab_proxy.py vm1            -> inicia em segundo plano e devolve o terminal
#   python colab_proxy.py vm1 --stop     -> encerra o proxy dessa sessão
#   python colab_proxy.py vm1 --status   -> mostra se está rodando
#   python colab_proxy.py vm1 --idle-timeout 30   -> proxy se encerra após 30 min sem uso
import argparse, asyncio, json, os, signal, socket, subprocess, sys, time
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("COLAB_CLI_CONFIG_DIR", Path.home() / ".config" / "colab-cli"))
STATE = CONFIG_DIR / "sessions.json"
SKIP = {"host", "origin", "content-length", "connection", "upgrade",
        "sec-websocket-key", "sec-websocket-version",
        "sec-websocket-extensions", "sec-websocket-protocol"}


# ----------------------------------------------------------------- utilidades
def pid_file(name): return CONFIG_DIR / f"proxy-{name}.pid"
def log_file(name): return CONFIG_DIR / f"proxy-{name}.log"

def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False

def load_entry(name: str) -> dict:
    return json.loads(STATE.read_text(encoding="utf-8"))[name]

def tail(path: Path, n=15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


# --------------------------------------------------------------------- servidor
def serve(name: str, port: int, idle_min: float):
    import aiohttp
    from aiohttp import web, WSMsgType
    from yarl import URL

    state = {"last": time.time(), "ws": 0}

    def target():
        e = load_entry(name)                      # relido a cada request (token renovado)
        return e["url"].rstrip("/"), e["token"]

    async def handler(request: web.Request):
        state["last"] = time.time()
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
                    protocols=[ws_cli.protocol] if ws_cli.protocol else [], max_msg_size=0)
                await ws_srv.prepare(request)

                async def pump(src, dst, label):
                    n = 0
                    try:
                        async for m in src:
                            n += 1
                            state["last"] = time.time()
                            if m.type == WSMsgType.TEXT:
                                await dst.send_str(m.data)
                            elif m.type == WSMsgType.BINARY:
                                await dst.send_bytes(m.data)
                            else:
                                break
                    finally:
                        print(f"WS {label} encerrou ({n} msgs)", flush=True)
                        await dst.close()

                state["ws"] += 1
                try:
                    await asyncio.gather(pump(ws_srv, ws_cli, "vscode->colab"),
                                         pump(ws_cli, ws_srv, "colab->vscode"))
                finally:
                    state["ws"] -= 1
                    await ws_cli.close()
                return ws_srv

            body = await request.read()
            async with s.request(request.method, base + request.path_qs, headers=headers,
                                 data=body, allow_redirects=False) as r:
                data = await r.read()
                out = {k: v for k, v in r.headers.items()
                       if k.lower() not in ("content-encoding", "transfer-encoding",
                                            "content-length", "connection")}
                print(request.method, request.path, "->", r.status, flush=True)
                return web.Response(status=r.status, body=data, headers=out)

    def cleanup_pid():
        try:
            info = json.loads(pid_file(name).read_text())
            if info.get("pid") == os.getpid():
                pid_file(name).unlink()
        except Exception:
            pass

    async def watchdog():
        while True:
            await asyncio.sleep(30)
            if idle_min > 0 and state["ws"] == 0 and time.time() - state["last"] > idle_min * 60:
                print(f"Ocioso por {idle_min} min: encerrando.", flush=True)
                cleanup_pid()
                os._exit(0)

    async def on_startup(app):
        app["watchdog"] = asyncio.create_task(watchdog())

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(lambda app: cleanup_pid())
    app.router.add_route("*", "/{tail:.*}", handler)
    print(f"Proxy '{name}' em http://127.0.0.1:{port} (pid {os.getpid()})", flush=True)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


# --------------------------------------------------------------------- launcher
def start(name: str, port: int, idle_min: float) -> int:
    try:
        load_entry(name)
    except FileNotFoundError:
        print(f"Erro: {STATE} não existe. Rode o script de sincronização antes."); return 1
    except KeyError:
        print(f"Erro: sessão '{name}' não está em {STATE}."); return 1

    if port_open(port):
        print(f"A porta {port} já está em uso (proxy já rodando?). Use --status ou --stop."); return 0

    cmd = [sys.executable, str(Path(__file__).resolve()), name,
           "--port", str(port), "--idle-timeout", str(idle_min), "--serve"]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True

    log = open(log_file(name), "ab")
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         close_fds=True, **kwargs)
    pid_file(name).write_text(json.dumps({"pid": p.pid, "port": port}))

    deadline = time.time() + 15
    while time.time() < deadline:
        if p.poll() is not None:
            print(f"O proxy encerrou ao iniciar (código {p.returncode}). Últimas linhas do log:\n{tail(log_file(name))}")
            return 1
        if port_open(port):
            print(f"Proxy '{name}' rodando em segundo plano (pid {p.pid}).")
            print(f"  VS Code: Select Kernel > Existing Jupyter Server > http://127.0.0.1:{port}")
            print(f"  Log:     {log_file(name)}")
            print(f"  Parar:   python {Path(__file__).name} {name} --stop")
            return 0
        time.sleep(0.3)

    print("Tempo esgotado esperando a porta abrir. Log:\n" + tail(log_file(name)))
    return 1


def stop(name: str) -> int:
    try:
        info = json.loads(pid_file(name).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"Nenhum pid registrado para '{name}'."); return 1
    try:
        os.kill(info["pid"], signal.SIGTERM)   # no Windows, equivale a terminar o processo
        print(f"Proxy '{name}' (pid {info['pid']}) encerrado.")
    except (ProcessLookupError, OSError):
        print("O processo já não existia.")
    pid_file(name).unlink(missing_ok=True)
    return 0


def status(name: str, port: int) -> int:
    try:
        info = json.loads(pid_file(name).read_text())
        port = info.get("port", port)
    except (FileNotFoundError, json.JSONDecodeError):
        info = None
    up = port_open(port)
    print(f"'{name}': {'rodando' if up else 'parado'} na porta {port}"
          + (f" (pid {info['pid']})" if info and up else ""))
    return 0 if up else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Proxy local Jupyter -> runtime do Colab")
    ap.add_argument("name", nargs="?", default="vm0", help="chave em sessions.json")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--idle-timeout", type=float, default=0, help="minutos sem uso para encerrar (0 = nunca)")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)  # uso interno
    a = ap.parse_args()

    if a.stop:
        sys.exit(stop(a.name))
    if a.status:
        sys.exit(status(a.name, a.port))
    if a.serve:
        serve(a.name, a.port, a.idle_timeout)
    else:
        sys.exit(start(a.name, a.port, a.idle_timeout))
