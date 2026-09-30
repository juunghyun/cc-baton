"""install / uninstall: 질문 없이 돌아가고, 여러 번 돌려도 같고, 남의 설정은 건드리지 않고, 걷을 땐 우리 것만."""
import json
import subprocess

import pytest

EXE = "/opt/cc-baton/bin/cc-baton"
FOREIGN_STATUS = {"type": "command", "command": "~/my-status.sh"}
FOREIGN_HOOK = {"type": "command", "command": "echo mine"}


@pytest.fixture
def inst(home):
    def run(*args):
        r = home.run(*args, CC_BATON_BIN=EXE)
        assert r.returncode == 0, r.stdout + r.stderr
        return r
    home.settings = home.root / ".claude/settings.json"
    home.zshrc = home.root / ".zshrc"
    home.commands = home.root / ".claude/commands"
    return run


def settings(home):
    return json.loads(home.settings.read_text())


def our_commands(data):
    return [h["command"] for groups in data.get("hooks", {}).values() for g in groups for h in g["hooks"]
            if "cc-baton" in h["command"]]


def test_install_on_fresh_home_is_idempotent(home, inst):
    inst("install")
    first = settings(home)
    assert first["statusLine"]["command"] == f"{EXE} statusline"
    assert sorted(our_commands(first)) == [f"{EXE} swap hook limit", f"{EXE} swap hook prompt"]
    assert first["hooks"]["StopFailure"][0]["matcher"] == "rate_limit"
    swap_md = (home.commands / "swap.md").read_text()
    assert f"Bash({EXE} swap:*)" in swap_md and "{cc_baton}" not in swap_md
    assert home.zshrc.read_text().count("# >>> cc-baton >>>") == 1

    inst("install")
    assert settings(home) == first
    assert home.zshrc.read_text().count("# >>> cc-baton >>>") == 1


def test_install_keeps_foreign_statusline_hooks_and_commands(home, inst):
    home.write_json(".claude/settings.json", {
        "statusLine": FOREIGN_STATUS,
        "hooks": {"UserPromptSubmit": [{"hooks": [FOREIGN_HOOK]}]},
        "model": "opus",
    })
    home.commands.mkdir(parents=True)
    (home.commands / "sleep.md").write_text("my own sleep command\n")
    home.zshrc.write_text("export FOO=1\n")

    inst("install")
    data = settings(home)
    assert data["statusLine"] == FOREIGN_STATUS and data["model"] == "opus"
    assert FOREIGN_HOOK in [h for g in data["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert (home.commands / "sleep.md").read_text() == "my own sleep command\n"
    assert home.zshrc.read_text().startswith("export FOO=1\n")
    assert list(home.settings.parent.glob("settings.json.bak-cc-baton-*"))  # 쓰기 전 백업


def test_install_refuses_broken_settings(home):
    home.settings = home.root / ".claude/settings.json"
    home.settings.parent.mkdir(parents=True, exist_ok=True)
    home.settings.write_text("{ broken")
    r = home.run("install", CC_BATON_BIN=EXE)
    assert r.returncode == 1
    assert home.settings.read_text() == "{ broken"


def test_uninstall_removes_only_ours_and_restores_statusline(home, inst):
    home.write_json(".claude/settings.json", {"statusLine": FOREIGN_STATUS,
                                              "hooks": {"UserPromptSubmit": [{"hooks": [FOREIGN_HOOK]}]}})
    home.zshrc.write_text("export FOO=1\n")
    inst("install")
    # 위저드에서 HUD 로 바꾼 상태를 흉내: 원래 statusline 은 install-state 에 기록돼 있다
    data = settings(home)
    data["statusLine"] = {"type": "command", "command": f"{EXE} statusline"}
    home.settings.write_text(json.dumps(data))

    inst("uninstall", "--yes", "--keep-package")
    data = settings(home)
    assert data["statusLine"] == FOREIGN_STATUS
    assert our_commands(data) == [] and data["hooks"]["UserPromptSubmit"] == [{"hooks": [FOREIGN_HOOK]}]
    assert "StopFailure" not in data["hooks"]
    assert not (home.commands / "swap.md").exists()
    assert home.zshrc.read_text() == "export FOO=1\n"
    assert home.config.exists()  # 설정은 기본으로 남긴다


def test_uninstall_purge_removes_config_and_state(home, inst):
    inst("install")
    home.state.mkdir(parents=True, exist_ok=True)
    inst("uninstall", "--yes", "--purge", "--keep-package")
    assert not home.config.parent.exists() and not home.state.exists()
    assert "statusLine" not in settings(home)
    assert (home.root / ".claude-swap-backup/sequence.json").exists()  # 계정 데이터는 절대 안 지운다


def test_install_merges_legacy_state_dirs(home, inst):
    old = home.root / ".local/state/cc-swap"
    (old / "sub").mkdir(parents=True)
    (old / "request.json").write_text("old-marker")
    (old / "sub/keep.txt").write_text("x")
    home.state.mkdir(parents=True)
    (home.state / "usage-refresh.stamp").write_text("new")  # 새 코드가 먼저 만든 폴더

    inst("install")
    assert old.is_symlink() and old.resolve() == home.state.resolve()
    assert (old / "request.json").read_text() == "old-marker"  # 옛 탭의 marker 경로도 그대로 통한다
    assert (home.state / "sub/keep.txt").read_text() == "x"
    assert (home.state / "usage-refresh.stamp").read_text() == "new"


def test_zsh_block_defines_baton(home, inst):
    inst("install")
    r = subprocess.run(["zsh", "-fc", f'source {home.zshrc}; whence -w baton; echo "$CC_BATON_BIN"'],
                       capture_output=True, text=True, env=home.env())
    assert r.stdout.split() == ["baton:", "function", EXE]


def test_setup_without_terminal_does_not_block(home, inst):
    r = home.run("setup", "--if-needed", CC_BATON_BIN=EXE)
    assert r.returncode == 0
    assert "setup" not in json.loads(home.config.read_text())  # 끝낸 걸로 치지 않는다


def test_setup_wizard_in_terminal(home, inst):
    """진짜 터미널(pty)에서 위저드에 답하면 설정·launchd 가 그대로 반영된다."""
    import fcntl, os, pty, select, struct, sys, termios, time  # noqa: E401
    inst("install")
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-m", "cc_baton", "setup", "--if-needed"],
                  home.env(CC_BATON_BIN=EXE))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    out = b""

    def pump(sec):
        nonlocal out
        end = time.time() + sec
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return

    # 그룹: personal → side, team → 엔터(그대로) / 절전: 켜고 45분 / 자동 스왑: 끔
    for answer in (b"ko\r", b"side\r", b"\r", b"y\r", b"45\r", b"n\r"):
        pump(0.6)
        try:
            os.write(fd, answer)
        except OSError:  # 위저드가 먼저 끝났다 — 무엇을 찍고 끝났는지 보여준다
            raise AssertionError(out.decode(errors="ignore"))
    pump(1.0)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0, out.decode(errors="ignore")

    cfg = json.loads(home.config.read_text())
    assert cfg["1"]["group"] == "side" and cfg["2"]["group"] == "team"
    assert cfg["hibernate"] == {"enabled": True, "idleMin": 45.0}
    assert cfg["onLimit"]["enabled"] is False and cfg["setup"]["done"] is True and cfg["language"] == "ko"
    import plistlib
    plist = plistlib.loads((home.root / "Library/LaunchAgents/io.github.juunghyun.cc-baton.hib.plist").read_bytes())
    assert plist["ProgramArguments"] == [EXE, "hib", "tick"]

    r = home.run("setup", "--if-needed", CC_BATON_BIN=EXE)  # 한 번 끝냈으면 다시 묻지 않는다
    assert r.returncode == 0 and r.stderr == ""


def test_lang_command_switches_config_and_hook_messages(home, inst):
    home.set_config(language="en")
    home.run("install", CC_BATON_BIN=EXE, CC_BATON_LANG="")
    msgs = [h.get("statusMessage") for g in settings(home)["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert "checking /swap" in msgs

    assert home.run("lang", "ko", CC_BATON_BIN=EXE, CC_BATON_LANG="").returncode == 0
    assert json.loads(home.config.read_text())["language"] == "ko"
    msgs = [h.get("statusMessage") for g in settings(home)["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert "/swap 확인 중" in msgs
    assert home.run("lang", CC_BATON_LANG="").stdout.strip() == "ko"
