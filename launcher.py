#!/usr/bin/env python3
"""
Funding Arb Bot — Launcher with System Tray
============================================
Starts both backend servers and provides a tray icon for quick access.

Usage:
    python launcher.py          # Start everything + tray icon
    python launcher.py --no-tray # Start servers only (headless)
"""
import atexit
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
DASHBOARD = ROOT / "funding-arb (1).html"
ICON_SVG = ROOT / "icon.svg"
ICON_PNG = ROOT / "icon.png"

BOT_SERVER_PORT = 8790
PRIVATE_SERVER_PORT = 8787

# ---------- process management ----------

processes: list[subprocess.Popen] = []


def kill_existing():
    """Kill any existing instances on our ports."""
    for port in (BOT_SERVER_PORT, PRIVATE_SERVER_PORT):
        try:
            result = subprocess.run(
                ["fuser", f"{port}/tcp"],
                capture_output=True, text=True, timeout=5
            )
            pids = result.stdout.strip().split()
            for pid in pids:
                pid = pid.strip()
                if pid.isdigit():
                    print(f"  Matando proceso existente PID {pid} en puerto {port}")
                    os.kill(int(pid), signal.SIGTERM)
            if pids:
                time.sleep(0.5)
        except Exception:
            pass


def start_server(script: str, name: str) -> subprocess.Popen:
    """Start a server subprocess."""
    log = open(ROOT / f".{name}.log", "w")
    proc = subprocess.Popen(
        [PYTHON, str(ROOT / script)],
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        preexec_fn=os.setsid,  # own process group for clean kill
    )
    processes.append(proc)
    print(f"  ✅ {name} iniciado (PID {proc.pid})")
    return proc


def stop_all():
    """Gracefully stop all child processes."""
    print("\n🛑 Deteniendo servidores...")
    for proc in processes:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
    for proc in processes:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
    print("  Listo.")


def wait_for_server(port: int, timeout: int = 15) -> bool:
    """Wait until a server responds on the given port."""
    import urllib.error
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def open_dashboard():
    """Open the dashboard in the default browser."""
    path = DASHBOARD.as_uri() if hasattr(DASHBOARD, 'as_uri') else f"file://{DASHBOARD}"
    webbrowser.open(path)
    print("  🌐 Dashboard abierto en browser")


def check_health() -> dict:
    """Quick health check of both servers."""
    import json
    import urllib.request
    status = {}
    for name, port in [("bot", BOT_SERVER_PORT), ("private", PRIVATE_SERVER_PORT)]:
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3)
            data = json.loads(resp.read())
            status[name] = "✅ OK" if data.get("ok") else f"⚠️ {data}"
        except Exception as e:
            status[name] = f"❌ {e}"
    return status


# ---------- generate PNG icon from SVG ----------

def ensure_icon_png():
    """Convert SVG to PNG for pystray (needs Pillow or cairosvg)."""
    if ICON_PNG.exists():
        return ICON_PNG
    try:
        # Try cairosvg first
        import cairosvg
        cairosvg.svg2png(url=str(ICON_SVG), write_to=str(ICON_PNG), output_width=64, output_height=64)
        return ICON_PNG
    except ImportError:
        pass
    # Fallback: generate a simple icon with Pillow
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGBA", (64, 64), (26, 26, 46, 255))
        draw = ImageDraw.Draw(img)
        # Dark blue circle background
        draw.ellipse([2, 2, 62, 62], fill=(22, 33, 62, 255), outline=(58, 123, 213, 255), width=2)
        # Up arrow (cyan)
        draw.line([(20, 38), (20, 18)], fill=(0, 210, 255), width=3)
        draw.line([(14, 24), (20, 18), (26, 24)], fill=(0, 210, 255), width=3)
        # Down arrow (gold)
        draw.line([(44, 26), (44, 46)], fill=(255, 210, 0), width=3)
        draw.line([(38, 40), (44, 46), (50, 40)], fill=(255, 210, 0), width=3)
        # Dollar sign
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 16)
        except Exception:
            font = ImageFont.load_default()
        draw.text((27, 24), "$", fill=(0, 255, 136), font=font)
        img.save(str(ICON_PNG))
        return ICON_PNG
    except Exception as e:
        print(f"  ⚠️ No se pudo generar icono PNG: {e}")
        return None


# ---------- system tray ----------

def run_tray():
    """Run the system tray icon."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        print("  ⚠️ pystray no instalado. Corriendo sin tray icon.")
        print("     Instalá con: pip install pystray Pillow")
        return

    icon_path = ensure_icon_png()
    if icon_path:
        image = Image.open(str(icon_path))
    else:
        # Minimal fallback
        image = Image.new("RGB", (64, 64), (26, 26, 46))

    def on_open_dashboard(icon, item):
        open_dashboard()

    def on_check_health(icon, item):
        status = check_health()
        msg = "\n".join(f"{k}: {v}" for k, v in status.items())
        try:
            subprocess.run(["notify-send", "Funding Arb Bot", msg, "-i", str(ICON_SVG)], timeout=5)
        except Exception:
            print(msg)

    def on_view_bot_log(icon, item):
        log_file = ROOT / ".bot_server.log"
        if log_file.exists():
            subprocess.Popen(["xdg-open", str(log_file)])

    def on_view_private_log(icon, item):
        log_file = ROOT / ".private_server.log"
        if log_file.exists():
            subprocess.Popen(["xdg-open", str(log_file)])

    def on_restart(icon, item):
        try:
            subprocess.run(["notify-send", "Funding Arb Bot", "🔄 Reiniciando...", "-i", str(ICON_SVG)], timeout=5)
        except Exception:
            pass
        stop_all()
        processes.clear()
        time.sleep(1)
        kill_existing()
        start_server("funding_arb_server.py", "bot_server")
        start_server("private_backend.py", "private_server")
        ok1 = wait_for_server(BOT_SERVER_PORT)
        ok2 = wait_for_server(PRIVATE_SERVER_PORT)
        msg = f"Bot: {'✅' if ok1 else '❌'}  Private: {'✅' if ok2 else '❌'}"
        try:
            subprocess.run(["notify-send", "Funding Arb Bot", f"Reiniciado. {msg}", "-i", str(ICON_SVG)], timeout=5)
        except Exception:
            print(msg)

    def on_quit(icon, item):
        stop_all()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("🌐 Abrir Dashboard", on_open_dashboard, default=True),
        pystray.MenuItem("❤️ Health Check", on_check_health),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("📋 Log Bot Server", on_view_bot_log),
        pystray.MenuItem("📋 Log Private Server", on_view_private_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🔄 Reiniciar Servidores", on_restart),
        pystray.MenuItem("🛑 Salir", on_quit),
    )

    icon = pystray.Icon("funding-arb", image, "Funding Arb Bot", menu)
    icon.run()


# ---------- main ----------

def main():
    no_tray = "--no-tray" in sys.argv

    print("=" * 50)
    print("  🚀 Funding Arb Bot — Launcher")
    print("=" * 50)

    # 1. Kill existing
    print("\n1️⃣  Limpiando procesos existentes...")
    kill_existing()

    # 2. Start servers
    print("\n2️⃣  Iniciando servidores...")
    start_server("funding_arb_server.py", "bot_server")
    start_server("private_backend.py", "private_server")

    # 3. Wait for servers
    print("\n3️⃣  Esperando que los servidores estén listos...")
    ok1 = wait_for_server(BOT_SERVER_PORT)
    ok2 = wait_for_server(PRIVATE_SERVER_PORT)
    print(f"  Bot Server (:{BOT_SERVER_PORT}):     {'✅ OK' if ok1 else '❌ TIMEOUT'}")
    print(f"  Private Server (:{PRIVATE_SERVER_PORT}): {'✅ OK' if ok2 else '❌ TIMEOUT'}")

    if not ok1:
        print("\n  ⚠️  El bot server no respondió. Revisá .bot_server.log")

    # 4. Open dashboard
    print("\n4️⃣  Abriendo dashboard...")
    open_dashboard()

    # 5. Register cleanup
    atexit.register(stop_all)
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    # 6. Tray or wait
    if no_tray:
        print("\n✅ Corriendo en modo headless. Ctrl+C para detener.")
        try:
            while True:
                # Monitor child processes
                for proc in processes:
                    if proc.poll() is not None:
                        print(f"  ⚠️ Proceso {proc.pid} terminó con código {proc.returncode}")
                time.sleep(5)
        except KeyboardInterrupt:
            pass
    else:
        print("\n5️⃣  Iniciando tray icon...")
        print("  (Click derecho en el ícono de la barra para opciones)")
        print("  (Doble click para abrir el dashboard)")
        run_tray()

    stop_all()


if __name__ == "__main__":
    main()
