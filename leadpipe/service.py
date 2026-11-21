"""Run the pipeline 24/7 in the background, starting automatically with your computer.

    leadpipe service install     start now + start at every login, restart if it ever stops
    leadpipe service status      is it running?
    leadpipe service stop|start  pause / resume the background run
    leadpipe service uninstall   remove it
    leadpipe service logs        last lines of the log

Windows : Task Scheduler task "LeadPipeline" (at log-on + every 5 minutes "if not running", no console window,
          wakes the PC to run, runs on battery). macOS: a LaunchAgent with KeepAlive. Linux: a systemd user service.
While the engine runs it also asks the OS not to sleep (engine.keep_awake).
For true 24/7 when your laptop is closed or off, run it on a small cloud server (see the user guide).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .config import Config

TASK_NAME = "LeadPipeline"
MAC_LABEL = "com.leadautomationpipeline.engine"
LINUX_UNIT = "leadpipe.service"
log = logging.getLogger("leadpipe.service")


# ---------------------------------------------------------------------------
# Logging to a file (the background process has no console)
# ---------------------------------------------------------------------------
def setup_file_logging(cfg: Config, verbose: bool = False) -> Path:
    logdir = cfg.data_dir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    path = logdir / "leadpipe.log"
    root = logging.getLogger()
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        h = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.DEBUG if verbose else logging.INFO)
    return path


# ---------------------------------------------------------------------------
# Keep the computer awake while the engine runs
# ---------------------------------------------------------------------------
_caffeinate: subprocess.Popen | None = None


def keep_awake(enable: bool = True) -> str:
    global _caffeinate
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_AWAYMODE_REQUIRED = 0x80000000, 0x00000001, 0x00000040
            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)  # type: ignore[attr-defined]
            return "Windows: sleep blocked while running" if enable else "released"
        if system == "Darwin":
            if enable and _caffeinate is None and shutil.which("caffeinate"):
                _caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
                return "macOS: caffeinate running"
            if not enable and _caffeinate is not None:
                _caffeinate.terminate()
                _caffeinate = None
            return "released"
        if system == "Linux" and enable and shutil.which("systemd-inhibit") and _caffeinate is None:
            _caffeinate = subprocess.Popen(["systemd-inhibit", "--what=sleep:idle", "--who=leadpipe",
                                            "--why=24/7 lead engine", "--mode=block", "sleep", "infinity"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "Linux: sleep inhibited"
    except Exception as exc:  # never fatal
        return f"keep-awake unavailable: {exc}"
    return "not needed"


# ---------------------------------------------------------------------------
# The command the service runs
# ---------------------------------------------------------------------------
def _python(windowless: bool) -> str:
    exe = Path(sys.executable)
    if windowless and platform.system() == "Windows":
        pyw = exe.with_name("pythonw.exe")
        if pyw.exists():
            return str(pyw)
    return str(exe)


def service_command(cfg: Config) -> tuple[str, list[str], str]:
    cfg_path = str(cfg.config_path.resolve())
    return _python(True), ["-m", "leadpipe", "-c", cfg_path, "start", "--background"], str(cfg.home.resolve())


def windows_task_xml(cfg: Config) -> str:
    exe, args, workdir = service_command(cfg)
    dom, usr = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    user_id = escape(f"{dom}\\{usr}" if dom and usr else usr)
    uid_xml = f"<UserId>{user_id}</UserId>" if user_id else ""
    arg_str = " ".join(f'"{a}"' if (" " in a or not a) else a for a in args)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>LeadAutomationPipeline: finds leads, sends and follows up 24/7.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled>{uid_xml}</LogonTrigger>
    <TimeTrigger>
      <Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>2024-01-01T00:00:00</StartBoundary><Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author">{uid_xml}<LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(exe)}</Command><Arguments>{escape(arg_str)}</Arguments><WorkingDirectory>{escape(workdir)}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def mac_plist(cfg: Config) -> str:
    exe, args, workdir = service_command(cfg)
    logdir = cfg.data_dir / "logs"
    items = "".join(f"<string>{escape(a)}</string>" for a in [exe, *args])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{MAC_LABEL}</string>
  <key>ProgramArguments</key><array>{items}</array>
  <key>WorkingDirectory</key><string>{escape(workdir)}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>{escape(str(logdir / 'service.out.log'))}</string>
  <key>StandardErrorPath</key><string>{escape(str(logdir / 'service.err.log'))}</string>
</dict></plist>
"""


def systemd_unit(cfg: Config) -> str:
    exe, args, workdir = service_command(cfg)
    cmd = " ".join([exe, *[f'"{a}"' if " " in a else a for a in args]])
    return f"""[Unit]
Description=LeadAutomationPipeline (24/7 lead engine + dashboard)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory={workdir}
ExecStart={cmd}
Restart=always
RestartSec=20
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError as exc:
        return 127, str(exc)


# ---------------------------------------------------------------------------
# Public actions
# ---------------------------------------------------------------------------
def install(cfg: Config) -> str:
    cfg.data_dir.joinpath("logs").mkdir(parents=True, exist_ok=True)
    system = platform.system()
    if system == "Windows":
        xml_path = cfg.data_dir / "leadpipe-task.xml"
        xml_path.write_text(windows_task_xml(cfg), encoding="utf-16")
        code, out = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"])
        if code != 0:
            return f"Could not create the scheduled task: {out}"
        _run(["schtasks", "/Run", "/TN", TASK_NAME])
        return ("Installed. LeadPipeline now runs in the background, starts when you log in, and restarts itself "
                "within 5 minutes if it ever stops. Dashboard: http://localhost:%s" % cfg.get("dashboard.port", 8787))
    if system == "Darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mac_plist(cfg), encoding="utf-8")
        _run(["launchctl", "unload", str(path)])
        code, out = _run(["launchctl", "load", "-w", str(path)])
        if code != 0:
            return f"Could not load the LaunchAgent: {out}"
        return "Installed. It runs in the background, starts at login and restarts if it stops."
    if system == "Linux":
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / LINUX_UNIT).write_text(systemd_unit(cfg), encoding="utf-8")
        if not shutil.which("systemctl"):
            return (f"Wrote {unit_dir / LINUX_UNIT}, but systemctl isn't available here. "
                    "Use Docker or `nohup leadpipe start &` instead.")
        _run(["systemctl", "--user", "daemon-reload"])
        code, out = _run(["systemctl", "--user", "enable", "--now", LINUX_UNIT])
        if code != 0:
            return f"Could not start the service: {out}"
        user = os.environ.get("USER", "$USER")
        return ("Installed and running. To keep it running after you log out (servers), run once:\n"
                f"  sudo loginctl enable-linger {user}")
    return f"Unsupported system: {system}"


def uninstall(cfg: Config) -> str:
    system = platform.system()
    if system == "Windows":
        _run(["schtasks", "/End", "/TN", TASK_NAME])
        code, out = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        _kill_engine(cfg)
        return "Removed." if code == 0 else out
    if system == "Darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
        _run(["launchctl", "unload", "-w", str(path)])
        path.unlink(missing_ok=True)
        return "Removed."
    if system == "Linux":
        _run(["systemctl", "--user", "disable", "--now", LINUX_UNIT])
        (Path.home() / ".config" / "systemd" / "user" / LINUX_UNIT).unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"])
        return "Removed."
    return f"Unsupported system: {system}"


def stop(cfg: Config) -> str:
    system = platform.system()
    if system == "Windows":
        _run(["schtasks", "/Change", "/TN", TASK_NAME, "/DISABLE"])
        _run(["schtasks", "/End", "/TN", TASK_NAME])
        _kill_engine(cfg)
    elif system == "Darwin":
        _run(["launchctl", "unload", str(Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist")])
    elif system == "Linux":
        _run(["systemctl", "--user", "stop", LINUX_UNIT])
    return "Stopped. Run `leadpipe service start` to resume."


def start(cfg: Config) -> str:
    system = platform.system()
    if system == "Windows":
        _run(["schtasks", "/Change", "/TN", TASK_NAME, "/ENABLE"])
        code, out = _run(["schtasks", "/Run", "/TN", TASK_NAME])
        return "Started." if code == 0 else out
    if system == "Darwin":
        code, out = _run(["launchctl", "load", "-w", str(Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist")])
        return "Started." if code == 0 else out
    if system == "Linux":
        code, out = _run(["systemctl", "--user", "start", LINUX_UNIT])
        return "Started." if code == 0 else out
    return f"Unsupported system: {system}"


def _engine_pid(cfg: Config) -> int | None:
    lock = cfg.data_dir / "engine.lock"
    try:
        pid = int(lock.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
            return pid if str(pid) in out else None
        os.kill(pid, 0)
        return pid
    except (OSError, PermissionError):
        return None


def _kill_engine(cfg: Config) -> None:
    pid = _engine_pid(cfg)
    if not pid:
        return
    if platform.system() == "Windows":
        _run(["taskkill", "/PID", str(pid), "/F"])
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def installed(cfg: Config) -> bool:
    system = platform.system()
    if system == "Windows":
        return _run(["schtasks", "/Query", "/TN", TASK_NAME])[0] == 0
    if system == "Darwin":
        return (Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist").exists()
    if system == "Linux":
        return (Path.home() / ".config" / "systemd" / "user" / LINUX_UNIT).exists()
    return False


def status(cfg: Config) -> dict:
    pid = _engine_pid(cfg)
    return {"installed": installed(cfg), "running": bool(pid), "pid": pid, "system": platform.system(),
            "log": str(cfg.data_dir / "logs" / "leadpipe.log")}


def tail_log(cfg: Config, lines: int = 40) -> str:
    path = cfg.data_dir / "logs" / "leadpipe.log"
    if not path.exists():
        return "(no log yet)"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
