"""install · setup(첫 실행 위저드) · uninstall · upgrade.

설계 (docs/PLAN.md 5단계):
- install 은 질문 없이, 여러 번 돌려도 같은 결과. 에이전트가 설치해도 사용자 설정을 절대 바꾸지 않는다.
  명령 파일 · zshrc 블록 · 훅(추가만) · 비어 있을 때만 statusline · 상태 폴더 이관.
- setup 은 사람에게 묻는다: 계정 그룹, HUD 교체, 절전(launchd), 한도 자동 스왑, 따로 깔린 claude-swap 정리.
  `baton` 이 처음 실행될 때 --if-needed 로 불린다.
- uninstall 은 우리가 넣은 것만 걷고, 설치 때 기록해 둔 원래 statusline 을 되돌린다.
  계정 로그인 데이터(~/.claude-swap-backup)는 어떤 경우에도 건드리지 않는다.
"""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import i18n
from . import state as st
from .i18n import L

R, DIM, BOLD, GRN, YEL, RED = "\033[0m", "\033[2m", "\033[1m", "\033[38;5;42m", "\033[38;5;214m", "\033[38;5;196m"
DATA = Path(__file__).resolve().parent / "data"
CLAUDE_DIR = Path.home() / ".claude"
SETTINGS = CLAUDE_DIR / "settings.json"
COMMANDS_DIR = CLAUDE_DIR / "commands"
ZSHRC = Path(os.environ.get("ZDOTDIR") or Path.home()) / ".zshrc"
INSTALL_STATE = st.CONFIG_PATH.parent / "install-state.json"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
HIB_PLIST = LAUNCH_AGENTS / f"{st.HIB_LABEL}.plist"
HIB_LOG = st.STATE_DIR / "hib.log"
ZSH_BEGIN, ZSH_END = "# >>> cc-baton >>>", "# <<< cc-baton <<<"
CMD_MARK = "<!-- installed by cc-baton; `cc-baton uninstall` removes this file -->"



def our_hooks():
    """우리 훅: (이벤트, 그룹 추가 키, 하위명령, 훅 추가 키). statusMessage 는 Claude Code 화면에 보인다."""
    return [
        ("UserPromptSubmit", {}, "swap hook prompt",
         {"timeout": 10, "statusMessage": L("checking /swap", "/swap 확인 중")}),
        ("StopFailure", {"matcher": "rate_limit"}, "swap hook limit",
         {"timeout": 10, "statusMessage": L("rate limit reached, scheduling an account swap", "한도 도달 — 계정 스왑 예약 중")}),
    ]


def say(msg=""):
    print(msg, file=sys.stderr)


def bin_path():
    """훅·statusline·zshrc 에 박을 cc-baton 절대경로. PATH 에 기대지 않는다."""
    if os.environ.get("CC_BATON_BIN"):
        return os.environ["CC_BATON_BIN"]
    venv_bin = Path(sys.executable).parent / "cc-baton"
    on_path = shutil.which("cc-baton")
    if on_path and venv_bin.exists() and Path(on_path).resolve() == venv_bin.resolve():
        return on_path  # ~/.local/bin/cc-baton 처럼 사람이 읽기 좋은 쪽
    return str(venv_bin) if venv_bin.exists() else (on_path or "cc-baton")


def _ours(cmd):
    return "cc-baton" in str(cmd) or "/.claude/bin/cc-swap" in str(cmd)  # 뒤는 0.1 이전 수동 설치


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


class Settings:
    """~/.claude/settings.json. 링크면 실제 파일을 고친다. 쓰기 전 시각별 백업, 깨진 JSON 이면 손대지 않는다."""

    def __init__(self):
        self.path = SETTINGS.resolve() if SETTINGS.is_symlink() else SETTINGS
        raw = self.path.read_text() if self.path.exists() else "{}"
        self.data = json.loads(raw)  # 깨졌으면 여기서 예외 → 호출자가 멈춘다
        self.orig = json.dumps(self.data, sort_keys=True)

    def save(self):
        """→ 결과 설명 (보고용). 바뀐 게 없으면 None."""
        if json.dumps(self.data, sort_keys=True) == self.orig:
            return None
        backup = None
        if self.path.exists():
            backup = self.path.with_name(f"{self.path.name}.bak-cc-baton-{time.strftime('%Y%m%d%H%M%S')}")
            shutil.copy2(self.path, backup)
        _write_json(self.path, self.data)
        return L(f"backup {backup.name}", f"백업 {backup.name}") if backup else L("created", "새로 만듦")


def merge_hooks(data, exe):
    """우리 훅을 한 번씩만. 남의 훅은 그대로 두고, 예전 경로의 우리 훅은 새 경로로 바꾼다."""
    hooks = data.setdefault("hooks", {})
    for event, group_extra, sub, hook_extra in our_hooks():
        groups = [g for g in hooks.get(event, []) if isinstance(g, dict)]
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if not _ours(h.get("command"))]
        groups = [g for g in groups if g.get("hooks")]
        groups.append({**group_extra, "hooks": [{"type": "command", "command": f"{exe} {sub}", **hook_extra}]})
        hooks[event] = groups


def remove_hooks(data):
    for event, groups in list((data.get("hooks") or {}).items()):
        kept = []
        for g in groups:
            if isinstance(g, dict):
                g["hooks"] = [h for h in g.get("hooks", []) if not _ours(h.get("command"))]
                if not g["hooks"]:
                    continue
            kept.append(g)
        if kept:
            data["hooks"][event] = kept
        else:
            del data["hooks"][event]
    if data.get("hooks") == {}:
        del data["hooks"]


def our_statusline(exe):
    return {"type": "command", "command": f"{exe} statusline", "refreshInterval": 10}


def install_commands(exe):
    """→ [(파일, 결과)]. 남이 만든 같은 이름 파일은 건드리지 않는다."""
    out = []
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    for src in sorted((DATA / "commands").glob("*.md")):
        dst = COMMANDS_DIR / src.name
        body = src.read_text().replace("{cc_baton}", exe).rstrip("\n") + "\n\n" + CMD_MARK + "\n"
        if dst.exists() or dst.is_symlink():
            mine = dst.is_symlink() and "cc-baton" in os.readlink(dst) or (
                dst.exists() and CMD_MARK in dst.read_text())
            if not mine:
                out.append((dst, L("skipped (you already have a different command with this name)", "건너뜀 (같은 이름의 다른 명령이 있음)")))
                continue
            if dst.is_symlink():
                dst.unlink()
            elif dst.read_text() == body:
                out.append((dst, L("unchanged", "그대로")))
                continue
        dst.write_text(body)
        out.append((dst, L("installed", "설치")))
    return out


def zsh_block(exe):
    return (f'{ZSH_BEGIN}\nexport CC_BATON_BIN="{exe}"\n'
            f'[[ -f "{DATA / "baton.zsh"}" ]] && source "{DATA / "baton.zsh"}"\n{ZSH_END}\n')


def _strip_block(text):
    """우리 블록과 0.1 이전 수동 설치 줄을 뺀 zshrc."""
    lines, out, skip = text.split("\n"), [], False
    for line in lines:
        if line.strip() == ZSH_BEGIN:
            skip = True
            continue
        if skip:
            skip = line.strip() != ZSH_END
            continue
        if "cc-baton/shell/cc.zsh" in line or line.startswith("# cc-baton: 계정 선택"):
            continue
        out.append(line)
    return "\n".join(out)


def install_zshrc(exe):
    text = ZSHRC.read_text() if ZSHRC.exists() else ""
    new = _strip_block(text).rstrip("\n") + "\n\n" + zsh_block(exe)
    if new.lstrip("\n") == text or new == text:
        return L("unchanged", "그대로")
    ZSHRC.write_text(new.lstrip("\n"))
    return L("added", "추가") if ZSH_BEGIN not in text else L("updated", "갱신")


def _merge_into(old, new):
    """old 폴더 내용을 new 로 합친다. 같은 이름 파일은 new 쪽을 남긴다 (새 코드가 이미 쓴 최신값)."""
    new.mkdir(parents=True, exist_ok=True)
    for child in old.iterdir():
        target = new / child.name
        if child.is_dir() and not child.is_symlink():
            _merge_into(child, target)
        elif not target.exists():
            child.rename(target)


def migrate_state():
    """0.1 이전 상태 폴더를 새 위치로 합치고 옛 경로엔 링크를 남긴다 (옛 셸 함수를 든 탭용).

    새 코드가 이미 돌았으면 새 폴더가 먼저 생겨 있을 수 있어서, 옮기지 않고 합친다.
    """
    moved = []
    for old, new in st.LEGACY_STATE.items():
        if old.is_dir() and not old.is_symlink():
            _merge_into(old, new)
            shutil.rmtree(old)
            old.symlink_to(new)
            moved.append(f"{old} → {new}")
    return moved


def link_cswap():
    """~/.local/bin/cswap 이 없으면 함께 설치된 claude-swap 으로 링크. 따로 깔린 게 있으면 위저드가 묻는다."""
    bundled = Path(sys.executable).parent / "cswap"
    link = Path.home() / ".local" / "bin" / "cswap"
    if not bundled.exists():
        return None
    if link.is_symlink() and link.resolve() == bundled.resolve():
        return L("unchanged", "그대로")
    if link.exists() or link.is_symlink():
        return L("left alone: you have claude-swap installed separately (cc-baton setup can clean it up)",
                 "따로 설치된 claude-swap 이 있어 그대로 둠 (cc-baton setup 에서 정리 가능)")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(bundled)
    return L("linked", "연결")


def claude_is_npm():
    c = shutil.which("claude")
    return bool(c) and "node_modules/@anthropic-ai/claude-code" in str(Path(c).resolve())


def hib_plist(exe):
    return {
        "Label": st.HIB_LABEL,
        "ProgramArguments": [exe, "hib", "tick"],
        "StartInterval": 180,
        "RunAtLoad": False,
        "StandardOutPath": str(HIB_LOG),
        "StandardErrorPath": str(HIB_LOG),
    }


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True).returncode


def register_hib(exe):
    HIB_LOG.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    HIB_PLIST.write_bytes(plistlib.dumps(hib_plist(exe)))
    uid = os.getuid()
    _launchctl("bootout", f"gui/{uid}/{st.HIB_LABEL}")
    return _launchctl("bootstrap", f"gui/{uid}", str(HIB_PLIST)) == 0


def unregister_hib():
    _launchctl("bootout", f"gui/{os.getuid()}/{st.HIB_LABEL}")
    if HIB_PLIST.exists():
        HIB_PLIST.unlink()
        return True
    return False


# ── install ──────────────────────────────────────────────────────────────
def save_config(**top):
    cfg = st.config()
    cfg.update(top)
    _write_json(st.config_path(), cfg)
    return cfg


def cmd_install(argv):
    exe = bin_path()
    try:
        s = Settings()
    except json.JSONDecodeError as e:
        say(f"{RED}✗{R} " + L(f"{SETTINGS} isn't valid JSON, so it was left alone ({e}). Fix it and run again.",
                              f"{SETTINGS} 가 올바른 JSON 이 아니라 손대지 않았습니다 ({e}). 고친 뒤 다시 실행하세요."))
        return 1

    report = []
    report += [L(f"state folder moved: {m}", f"상태 폴더 이관: {m}") for m in migrate_state()]

    merge_hooks(s.data, exe)
    inst = _read_json(INSTALL_STATE, {})
    cur = s.data.get("statusLine")
    if not cur:
        s.data["statusLine"] = our_statusline(exe)
        report.append(L("HUD (statusline): installed", "HUD(statusline): 설치"))
    elif _ours(cur.get("command") if isinstance(cur, dict) else cur):
        s.data["statusLine"] = {**cur, **{"command": f"{exe} statusline"}}
    else:
        inst.setdefault("prevStatusLine", cur)
        report.append(L("HUD (statusline): you already have one, left as is. The setup wizard on first `baton` can switch it",
                        "HUD(statusline): 기존 statusline 이 있어 그대로 둠 — `baton` 첫 실행 위저드에서 바꿀 수 있음"))
    backup = s.save()
    report.append("settings.json: " + (L(f"hooks added ({backup})", f"훅 반영 ({backup})") if backup else L("no change", "변경 없음")))

    report += [L(f"command {p.name}: {r}", f"명령 {p.name}: {r}") for p, r in install_commands(exe)]
    report.append(L(f"~/.zshrc block: {install_zshrc(exe)}", f"~/.zshrc 블록: {install_zshrc(exe)}"))
    c = link_cswap()
    if c:
        report.append(L(f"cswap command: {c}", f"cswap 명령: {c}"))

    cfg = st.config()
    if "autoUpdate" not in cfg:  # npm 으로 깐 Claude Code 만 우리가 올린다. 공식 설치본은 자체 자동 업데이트
        save_config(autoUpdate={"enabled": claude_is_npm()})
    if HIB_PLIST.exists():  # 이미 켜 둔 절전만 새 경로로 갱신. 새로 등록은 위저드에서만
        register_hib(exe)
        report.append(L("hibernate launchd agent: updated to the new path", "절전 launchd: 새 경로로 갱신"))

    inst["installedAt"] = time.time()
    inst["exe"] = exe
    _write_json(INSTALL_STATE, inst)

    say(f"{GRN}✓{R} " + L("cc-baton installed", "cc-baton 설치"))
    for line in report:
        say(f"  {DIM}·{R} {line}")
    done = st.config().get("setup", {}).get("done")
    say("\n  " + L(f"Open a new terminal and run {BOLD}baton{R}.", f"새 터미널을 열고 {BOLD}baton{R} 을 실행하세요.")
        + ("" if done else L(" The first run starts a short setup wizard.", " 처음 실행하면 설정 위저드가 뜹니다.")))
    return 0


# ── setup (첫 실행 위저드) ─────────────────────────────────────────────────
class Tty:
    def __init__(self):
        self.f = open("/dev/tty", "r+")

    def ask(self, prompt, default=""):
        self.f.write(prompt)
        self.f.flush()
        ans = self.f.readline()
        if not ans:
            raise EOFError
        return ans.strip() or default

    def yes(self, prompt, default=False):
        hint = "[Y/n]" if default else "[y/N]"
        return self.ask(f"{prompt} {DIM}{hint}{R} ", "y" if default else "n").lower().startswith("y")


def _preview_hud():
    sample = {"model": {"display_name": "Opus 5.5 (1M context)"}, "effort": {"level": "high"},
              "context_window": {"used_percentage": 12}, "workspace": {"current_dir": str(Path.cwd())}}
    r = subprocess.run([sys.executable, "-m", "cc_baton", "statusline"], input=json.dumps(sample),
                       capture_output=True, text=True, env={**os.environ, "CC_BATON_LANG": i18n.lang()})
    return "\n".join("    " + line for line in r.stdout.rstrip().split("\n"))


def _bundled_version():
    try:
        from importlib.metadata import version
        return version("claude-swap")
    except Exception:
        return ""


def _separate_claude_swap():
    try:
        out = subprocess.run(["uv", "tool", "list"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False
    return any(line.startswith("claude-swap ") for line in out.splitlines())


def _system_language():
    """macOS 의 선호 언어 첫 번째 (defaults read -g AppleLanguages). 모르면 로캘."""
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=3).stdout
        first = out.replace("(", "").replace('"', "").split(",")[0].strip().lower()
        if first:
            return "ko" if first.startswith("ko") else "en"
    except Exception:
        pass
    return i18n.detect()


def cmd_setup(argv):
    cfg = st.config()
    if "--if-needed" in argv and (cfg.get("setup") or {}).get("done"):
        return 0
    try:
        t = Tty()
    except OSError:
        say(DIM + L("[cc-baton] Run the setup wizard from a terminal: `cc-baton setup`.",
                    "[cc-baton] 설정 위저드는 터미널에서 `cc-baton setup` 으로 실행하세요.") + R)
        return 0
    exe = bin_path()
    w = lambda msg="": (t.f.write(msg + "\n"), t.f.flush())  # noqa: E731
    try:
        # 0) 언어 (두 언어로 묻는다)
        cur_lang = cfg.get("language") if cfg.get("language") in i18n.LANGS else _system_language()
        ans = t.ask(f"\n{BOLD}Language / 언어{R} [en/ko] ({cur_lang}): ", cur_lang).lower()
        chosen = "ko" if ans.startswith(("ko", "k", "한")) else ("en" if ans.startswith(("en", "e")) else cur_lang)
        save_config(language=chosen)
        i18n.set_lang(chosen)

        w("\n" + BOLD + L("cc-baton setup", "cc-baton 설정") + R + " " + DIM
          + L("(Enter = the value in brackets. Run cc-baton setup any time to change)",
              "(엔터 = 괄호 안 기본값, 나중에 cc-baton setup 으로 다시)") + R + "\n")

        # 1) 계정 그룹
        cfg = st.config()
        accs = st.accounts()
        w(BOLD + L("1. Account groups", "1. 계정 그룹") + R + " " + DIM
          + L("— swapping to an account in another group asks you first (e.g. work, personal)",
              "— 그룹이 다른 계정으로 스왑할 땐 한 번 더 확인합니다 (예: work, personal)") + R)
        if not accs:
            w("  " + L(f"No accounts in claude-swap yet. Register the account you're logged in with: {BOLD}cswap add{R}",
                       f"claude-swap 에 등록된 계정이 없습니다. 지금 로그인된 계정을 {BOLD}cswap add{R} 로 먼저 등록하세요."))
        for a in accs:
            cur = st.group(a["num"])
            shown = "" if cur == "unknown" else cur
            g = t.ask(f"  {a['num']}) {a['label']:<14} " + L("group", "그룹") + f" [{shown}]: ", shown)
            if g:
                entry = cfg.setdefault(a["num"], {})
                entry["group"] = g.lower()
                entry.pop("kind", None)
        if len(accs) == 1:
            w("  " + DIM + L("To add another account: log in to it with /login in Claude Code, then run cswap add",
                              "계정을 더 쓰려면: Claude Code 에서 /login 으로 다른 계정에 로그인한 뒤 cswap add") + R)
        _write_json(st.config_path(), cfg)

        # 2) HUD
        w(f"\n{BOLD}2. HUD (statusline){R}")
        s = Settings()
        cur = s.data.get("statusLine")
        if cur and not _ours(cur.get("command") if isinstance(cur, dict) else cur):
            w("  " + L("You already have a statusline. This is what the cc-baton HUD looks like:",
                       "이미 쓰시는 statusline 이 있습니다. cc-baton HUD 는 이렇게 보입니다:") + f"\n{_preview_hud()}")
            if t.yes("  " + L("Switch to the cc-baton HUD? (uninstall puts yours back)",
                              "cc-baton HUD 로 바꿀까요? (지울 때 원래 것으로 되돌립니다)")):
                inst = _read_json(INSTALL_STATE, {})
                inst.setdefault("prevStatusLine", cur)
                _write_json(INSTALL_STATE, inst)
                s.data["statusLine"] = our_statusline(exe)
                s.save()
                w("  → " + L("switched", "바꿨습니다"))
        else:
            if not cur:
                s.data["statusLine"] = our_statusline(exe)
                s.save()
            w("  → " + L("in place", "적용돼 있습니다"))

        # 3) 절전
        w("\n" + BOLD + L("3. Hibernate", "3. 절전") + R + " " + DIM
          + L("— stops the process of sessions left idle, keeps the tab and the conversation. Any key resumes",
              "— 오래 쉬는 세션의 프로세스를 끄고, 탭과 대화는 남깁니다. 키 하나로 이어서 시작") + R)
        hib = st.feature("hibernate")
        if t.yes("  " + L("Hibernate idle sessions automatically?", "유휴 세션 자동 재우기를 켤까요?"), hib["enabled"]):
            m = t.ask("  " + L("After how many idle minutes?", "몇 분 쉬면 재울까요?") + f" [{hib['idleMin']:g}]: ",
                      str(hib["idleMin"]))
            try:
                idle = max(5.0, float(m))
            except ValueError:
                idle = float(hib["idleMin"])
            cfg = st.config()
            save_config(hibernate={**(cfg.get("hibernate") or {}), "enabled": True, "idleMin": idle})
            w("  → " + L("on", "켰습니다") if register_hib(exe) else
              f"  {YEL}→ " + L("on, but registering the launchd agent failed (the HUD shows ⚠)",
                               "켰지만 launchd 등록에 실패했습니다 (HUD 에 ⚠ 로 보입니다)") + R)
        else:
            cfg = st.config()
            save_config(hibernate={**(cfg.get("hibernate") or {}), "enabled": False})
            unregister_hib()
            w("  → " + L("off (/sleep still works any time)", "껐습니다 (/sleep 으로 직접 재우기는 됩니다)"))

        # 4) 한도 자동 스왑
        w("\n" + BOLD + L("4. Auto-swap on rate limits", "4. 한도 도달 시 자동 스왑") + R + " " + DIM
          + L("— when you hit the 5h or weekly limit, schedules a swap to an account with room left",
              "— 5h·주간 한도에 걸리면 여유 있는 계정으로 스왑을 예약합니다") + R)
        w(f"  {YEL}" + L("Check for yourself whether using several accounts around usage limits fits Anthropic's terms.",
                         "여러 계정으로 사용 한도를 넘나드는 게 Anthropic 약관에 맞는지는 직접 확인하세요.") + R)
        lim = st.feature("onLimit")
        on = t.yes("  " + L("Turn it on?", "켤까요?"), lim["enabled"])
        cfg = st.config()
        save_config(onLimit={**(cfg.get("onLimit") or {}), "enabled": on})
        w("  → " + (L("on", "켰습니다") if on else L("off (/swap still works any time)", "껐습니다 (/swap 으로 직접 바꾸기는 됩니다)")))

        # 5) 따로 깔린 claude-swap
        if _separate_claude_swap():
            w("\n" + BOLD + "5. claude-swap" + R + " " + DIM
              + L("— you have claude-swap installed separately. Your account data stays as it is",
                  "— 따로 설치된 claude-swap 이 있습니다. 계정 데이터는 그대로 씁니다") + R)
            if t.yes("  " + L("Remove it and use the version bundled with cc-baton?", "정리하고 cc-baton 에 들어 있는 버전을 쓸까요?"), True):
                ok = subprocess.run(["uv", "tool", "uninstall", "claude-swap"], capture_output=True).returncode == 0
                if ok:
                    link_cswap()
                    v = _bundled_version()
                    w("  → " + L(f"Done. cswap is now the claude-swap {v} bundled with cc-baton",
                                 f"정리했습니다. cswap 은 이제 cc-baton 에 들어 있는 claude-swap {v} 입니다"))
                else:
                    w("  → " + L("Couldn't remove it. Run: uv tool uninstall claude-swap",
                                 "정리하지 못했습니다. 직접: uv tool uninstall claude-swap"))

        save_config(setup={"done": True, "at": time.time()})
        _refresh_hook_messages(exe)
        w(f"\n{GRN}✓{R} " + L(f"All set. Change things with {BOLD}cc-baton setup{R}, or one feature with {BOLD}cc-baton toggle{R}",
                              f"설정 끝. 바꾸려면 {BOLD}cc-baton setup{R}, 기능 하나만은 {BOLD}cc-baton toggle{R}") + "\n")
        return 0
    except (EOFError, KeyboardInterrupt):
        w("\n" + DIM + L("Setup stopped. It will ask again next time you run baton.",
                         "설정을 멈췄습니다. 다음 baton 실행 때 다시 묻습니다.") + R)
        return 1


def _refresh_hook_messages(exe):
    """훅의 statusMessage 는 설치 때 언어로 박힌다. 언어를 바꾸면 다시 맞춘다."""
    try:
        s = Settings()
    except json.JSONDecodeError:
        return
    merge_hooks(s.data, exe)
    s.save()


# ── uninstall ────────────────────────────────────────────────────────────
def cmd_uninstall(argv):
    yes, purge = "--yes" in argv, "--purge" in argv
    try:
        t = None if yes else Tty()
    except OSError:
        t = None
    ask = (lambda q, d: d) if t is None else (lambda q, d: t.yes(q, d))  # noqa: E731

    try:
        s = Settings()
    except json.JSONDecodeError as e:
        say(f"{RED}✗{R} " + L(f"{SETTINGS} isn't valid JSON, so it was left alone ({e}).",
                              f"{SETTINGS} 가 올바른 JSON 이 아니라 손대지 않았습니다 ({e})."))
        return 1
    inst = _read_json(INSTALL_STATE, {})
    report = []
    remove_hooks(s.data)
    cur = s.data.get("statusLine")
    if cur and _ours(cur.get("command") if isinstance(cur, dict) else cur):
        if inst.get("prevStatusLine"):
            s.data["statusLine"] = inst["prevStatusLine"]
            report.append(L("HUD: your previous statusline is back", "HUD: 원래 statusline 으로 되돌림"))
        else:
            del s.data["statusLine"]
            report.append(L("HUD: removed", "HUD: 뺌"))
    backup = s.save()
    report.append("settings.json: " + (L(f"our hooks removed ({backup})", f"우리 훅 제거 ({backup})") if backup
                                       else L("no change", "변경 없음")))

    for f in sorted(COMMANDS_DIR.glob("*.md")) if COMMANDS_DIR.exists() else []:
        if (f.is_symlink() and "cc-baton" in os.readlink(f)) or (f.exists() and CMD_MARK in f.read_text()):
            f.unlink()
            report.append(L(f"command {f.name}: removed", f"명령 {f.name}: 제거"))
    if ZSHRC.exists():
        text = ZSHRC.read_text()
        new = _strip_block(text)
        if new != text:
            ZSHRC.write_text(new.rstrip("\n") + "\n")
            report.append(L("~/.zshrc block: removed (open shells change once reopened)",
                            "~/.zshrc 블록: 제거 (열려 있는 셸은 새로 열면 반영)"))
    if unregister_hib():
        report.append(L("hibernate launchd agent: removed", "절전 launchd: 해제"))

    link = Path.home() / ".local" / "bin" / "cswap"
    bundled = Path(sys.executable).parent / "cswap"
    if link.is_symlink() and bundled.exists() and link.resolve() == bundled.resolve():
        link.unlink()
        if ask(L("Keep using claude-swap (cswap)? It will be installed on its own.",
                 "claude-swap(cswap) 은 계속 쓰시겠어요? 따로 다시 설치해 둡니다."), False):
            ok = subprocess.run(["uv", "tool", "install", "claude-swap>=0.25,<0.27"], capture_output=True).returncode == 0
            report.append(L("cswap: installed on its own", "cswap: 따로 설치") if ok
                          else L("cswap: install failed (uv tool install claude-swap)", "cswap: 설치 실패 (uv tool install claude-swap)"))
        else:
            report.append(L("cswap: removed (account data in ~/.claude-swap-backup is kept)",
                            "cswap: 제거 (계정 데이터 ~/.claude-swap-backup 은 그대로)"))

    if purge or ask(L("Also delete settings and state (account groups, state, hibernate log)?",
                      "설정·기록(계정 그룹, 상태, 절전 로그)까지 지울까요?"), False):
        for p in (st.CONFIG_PATH.parent, st.STATE_DIR, *st.LEGACY_STATE):
            if p.is_symlink():
                p.unlink()
            elif p.exists():
                shutil.rmtree(p)
        report.append(L("settings and state: deleted", "설정·기록: 삭제"))
    else:
        report.append(L(f"settings and state: kept ({st.CONFIG_PATH.parent})", f"설정·기록: 남김 ({st.CONFIG_PATH.parent})"))

    say(f"{GRN}✓{R} " + L("cc-baton wiring removed", "cc-baton 연결 제거"))
    for line in report:
        say(f"  {DIM}·{R} {line}")
    if "--keep-package" not in argv and ask(L("Remove the cc-baton package too?", "cc-baton 패키지도 지울까요?"), True):
        rc = subprocess.run(["uv", "tool", "uninstall", "cc-baton"], capture_output=True).returncode
        say(f"  {DIM}·{R} " + (L("package: removed", "패키지: 삭제") if rc == 0
                               else L("package: removal failed, run uv tool uninstall cc-baton", "패키지: 삭제 실패 — uv tool uninstall cc-baton")))
    return 0


# ── upgrade ──────────────────────────────────────────────────────────────
def cmd_upgrade(argv):
    rc = subprocess.call(["uv", "tool", "upgrade", "cc-baton"])
    if rc != 0:
        return rc
    return subprocess.call([bin_path(), "install"])  # 새 버전의 연결 형식으로 다시 맞춘다
