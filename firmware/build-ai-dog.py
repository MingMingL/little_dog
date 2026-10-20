#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Dog V1 firmware build driver -- Python port of build-ai-dog.ps1.

Why this exists next to the .ps1
--------------------------------
The .ps1 needs PowerShell. It cannot run when
  * PowerShell scripting is disabled (ExecutionPolicy=Restricted), or
  * the host cannot spawn native child processes from PowerShell.
Both are true on the current dev box, so this script is the way in.

How the toolchain is located
----------------------------
ESP-IDF installs made by Espressif Installation Manager (eim) write an
activation profile that already contains the fully resolved environment:
every tool path, IDF_PATH, IDF_PYTHON_ENV_PATH, ESP_ROM_ELF_DIR, ...

We read that profile rather than shelling out to `idf_tools.py export`,
because on an eim install the two disagree: idf_tools expects tools under
`IDF_TOOLS_PATH/tools/<name>/<version>`, eim puts them directly under
`<install>/tools/<name>/<version>`. Parsing the profile is exactly what the
PowerShell script achieves by dot-sourcing it -- and it is what is known to
work on this machine.

Two Windows/POSIX interop repairs are applied before starting the build:
  * MSYSTEM/MSYS are removed. ESP-IDF 6.x aborts under MSys/Mingw: idf.py
    only prints a warning and never reaches main(), and idf_tools.py exits
    outright.
  * PROCESSOR_ARCHITECTURE is restored when missing. POSIX shells do not
    pass it through, and platform.machine() then returns "", which makes
    ESP-IDF compute the platform as "Windows-" and refuse to continue.

Usage
-----
    python build-ai-dog.py                       # build
    python build-ai-dog.py flash   -p COM3
    python build-ai-dog.py monitor -p COM3
    python build-ai-dog.py menuconfig
    python build-ai-dog.py reconfigure
    python build-ai-dog.py fullclean

    --idf-path PATH           override ESP-IDF checkout
    --idf-tools-path PATH     override IDF_TOOLS_PATH
    --activate PATH           override the eim activation profile

IMPORTANT: run `reconfigure` after ADDING a new source file under
main/boards/ai-dog-s3/ -- CMake's file(GLOB BOARD_SOURCES) only collects
*.cc at configure time, so a brand-new file is silently left out of the
build until CMake re-runs.
"""

import argparse
import glob
import os
import pathlib
import platform
import re
import runpy
import sys

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE / "xiaozhi-esp32"
DEFAULTS = HERE / "config" / "ai-dog-s3.defaults"

ACTIVATE_CANDIDATES = [
    r"C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1",
    r"C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1",
    r"F:\software\esp\.espressif\v6.1\tools\Microsoft.v6.1.PowerShell_profile.ps1",
]

IDF_PATH_CANDIDATES = [
    r"F:\software\esp\.espressif\v6.1\esp-idf",
    r"C:\Espressif\frameworks\esp-idf-v6.1",
    r"C:\Espressif\esp-idf",
]

IDF_TOOLS_PATH_CANDIDATES = [
    r"F:\software\esp\.espressif\v6.1\tools",
    r"C:\Espressif\tools",
]

PAIR_RE = re.compile(r'^\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*=\s*"(.*?)"\s*$')
PATH_RE = re.compile(r'^\s*\$env:PATH\s*=\s*"(.*?)"\s*$')


def find_activate_script(explicit):
    if explicit:
        p = pathlib.Path(explicit)
        if not p.exists():
            sys.exit("ERROR: activation profile not found: %s" % p)
        return p

    for cand in ACTIVATE_CANDIDATES:
        p = pathlib.Path(cand)
        if p.exists():
            return p

    for pattern in (
        r"C:\Espressif\tools\Microsoft.*PowerShell_profile.ps1",
        r"F:\software\esp\.espressif\v*\tools\Microsoft.*PowerShell_profile.ps1",
    ):
        hits = sorted(glob.glob(pattern))
        if hits:
            return pathlib.Path(hits[-1])
    return None


def parse_activate_script(path):
    """Return (env_pairs, path_prefix_dirs) from an eim activation profile.

    The generator emits a very regular shape:

        $env_var_pairs = @{
            "IDF_PATH" = "F:\\...\\esp-idf"
            ...
        }
        ...
        $env:PATH = "F:\\...\\bin;F:\\...\\bin;$env:PATH"

    which is what we key off. Anything unparsable is skipped rather than
    guessed at.
    """
    text = path.read_text(encoding="utf-8", errors="replace")

    pairs = {}
    in_pairs_block = False
    path_prefix = []
    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("$env_var_pairs"):
            in_pairs_block = True
            continue
        if in_pairs_block:
            if stripped.startswith("}"):
                in_pairs_block = False
            else:
                m = PAIR_RE.match(stripped)
                if m and "$" not in m.group(2):
                    pairs[m.group(1)] = m.group(2)
            continue

        m = PATH_RE.match(stripped)
        if m:
            raw = m.group(1)
            raw = raw[: raw.rfind(";$env:PATH")] if ";$env:PATH" in raw else raw
            path_prefix = [p for p in raw.split(";") if p and "$" not in p]

    return pairs, path_prefix


def ensure_venv_python():
    """idf.py needs rich_click / click, which exist only inside the ESP-IDF
    virtualenv. If this script was started with some other python it dies with

        No module named 'rich_click'
        This usually means that "idf.py" was not spawned within an ESP-IDF
        shell environment ...

    so detect that case and hand over to the venv python instead of guessing.
    """
    if os.environ.get("AIDOG_BUILD_REEXECED"):
        return

    env_path = os.environ.get("IDF_PYTHON_ENV_PATH")
    if not env_path:
        profile = find_activate_script(None)
        if profile is not None:
            pairs, _ = parse_activate_script(profile)
            env_path = pairs.get("IDF_PYTHON_ENV_PATH")
    if not env_path:
        return

    venv_python = pathlib.Path(env_path) / "Scripts" / "python.exe"
    if not venv_python.exists():
        return

    try:
        if pathlib.Path(sys.executable).resolve() == venv_python.resolve():
            return
    except OSError:
        pass

    print("==> python is %s, re-exec under ESP-IDF venv: %s" % (sys.executable, venv_python),
          flush=True)
    os.environ["AIDOG_BUILD_REEXECED"] = "1"
    drop_msystems()
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)


VERSION_RE = re.compile(r'^set\(IDF_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)\)')


def apply_idf_version(idf):
    """The activation profile sets ESP_IDF_VERSION / IDF_VERSION by parsing
    tools/cmake/version.cmake, and idf_component_manager hard-requires
    ESP_IDF_VERSION (it calls Version.coerce() on it and blows up on None).
    Do the same parse."""
    versions = {}
    cmake_version = idf / "tools" / "cmake" / "version.cmake"
    try:
        for line in cmake_version.read_text(encoding="utf-8", errors="replace").splitlines():
            m = VERSION_RE.match(line.strip())
            if m:
                versions[m.group(1)] = m.group(2)
    except OSError:
        return

    major, minor = versions.get("MAJOR"), versions.get("MINOR")
    if not (major and minor):
        return
    os.environ["ESP_IDF_VERSION"] = "%s.%s" % (major, minor)
    if versions.get("PATCH"):
        os.environ["IDF_VERSION"] = "%s.%s.%s" % (major, minor, versions["PATCH"])
    else:
        os.environ["IDF_VERSION"] = "%s.%s" % (major, minor)


def repair_platform_detection():
    """platform.machine() on Windows is read from PROCESSOR_ARCHITECTURE.
    POSIX shells (Git Bash/MSYS) do not pass it through, so ESP-IDF computes
    PYTHON_PLATFORM as "Windows-" and idf_tools dies with
    "Support for platform 'Windows-' hasn't been added yet"."""
    if not platform.machine():
        os.environ["PROCESSOR_ARCHITECTURE"] = "AMD64"


def drop_msystems():
    """ESP-IDF 6.x refuses to run under MSys/Mingw. The shell that launched us
    is Git Bash, so these are always present and always have to go."""
    os.environ.pop("MSYSTEM", None)
    os.environ.pop("MSYS", None)


def apply_toolchain_environment(idf_arg, tools_arg, activate):
    repair_platform_detection()
    drop_msystems()

    if activate is not None:
        pairs, path_prefix = parse_activate_script(activate)
        for key, value in pairs.items():
            os.environ[key] = value
        if path_prefix:
            os.environ["PATH"] = ";".join(path_prefix) + ";" + os.environ.get("PATH", "")
        print("==> activation profile: %s (%d vars, %d PATH entries)"
              % (activate, len(pairs), len(path_prefix)))
        drop_msystems()

    if idf_arg:
        os.environ["IDF_PATH"] = idf_arg
    if tools_arg:
        os.environ["IDF_TOOLS_PATH"] = tools_arg

    idf = os.environ.get("IDF_PATH")
    if idf and (pathlib.Path(idf) / "tools" / "idf.py").exists():
        return pathlib.Path(idf)

    for cand in IDF_PATH_CANDIDATES:
        if (pathlib.Path(cand) / "tools" / "idf.py").exists():
            os.environ["IDF_PATH"] = cand
            return pathlib.Path(cand)

    sys.exit(
        "ERROR: ESP-IDF not found. Pass --idf-path, --activate, or set IDF_PATH.\n"
        "       Looked in: %s" % ", ".join(IDF_PATH_CANDIDATES)
    )


def apply_project_environment():
    """Workspace-local overrides documented in build-ai-dog.ps1. Keep them:
    they are what made the first build reproducible on Windows."""
    os.environ["SDKCONFIG_DEFAULTS"] = ";".join(
        ["sdkconfig.defaults", "sdkconfig.defaults.esp32s3", str(DEFAULTS)]
    )

    cache = HERE / ".idf-component-cache"
    cache.mkdir(exist_ok=True)
    os.environ["IDF_COMPONENT_CACHE_PATH"] = str(cache)

    os.environ["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"

    # Compiler scratch space. Defaults to a workspace dir (see build-ai-dog.ps1),
    # but AIDOG_BUILD_TMP overrides it:
    #   AIDOG_BUILD_TMP=""          -> leave TMP/TEMP alone (use the system temp)
    #   AIDOG_BUILD_TMP=D:\scratch  -> use that directory
    tmp_override = os.environ.get("AIDOG_BUILD_TMP")
    if tmp_override != "":
        tmp = pathlib.Path(tmp_override) if tmp_override else (HERE / ".build-tmp")
        tmp.mkdir(parents=True, exist_ok=True)
        os.environ["TMP"] = str(tmp)
        os.environ["TEMP"] = str(tmp)

    ccache = HERE / ".ccache"
    ccache.mkdir(exist_ok=True)
    os.environ["CCACHE_DIR"] = str(ccache)
    os.environ["CCACHE_DISABLE"] = "1"

    # Do not put ccache in front of the compiler at all. Its temp/response-file
    # handoff to the compiler failed here with
    #   xtensa-esp-elf-gcc.exe: fatal error: could not open temporary response
    #   file <workspace>\.build-tmp\ccXXXXXX
    # (the file was created empty and gcc could not open it). Since caching is
    # disabled anyway, ccache was pure overhead plus this failure mode.
    # idf.py turns this env var into -DCCACHE_ENABLE for CMake.
    os.environ["IDF_CCACHE_ENABLE"] = "0"

    os.environ["PYTHONUTF8"] = "1"


def run_idf(idf, args):
    # runpy.run_path does not put the script's own directory on sys.path the way
    # `python tools/idf.py` does, and idf.py imports python_version_checker from
    # there. Add it explicitly.
    tools_dir = str(idf / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    sys.argv = ["idf.py"] + args
    try:
        runpy.run_path(str(idf / "tools" / "idf.py"), run_name="__main__")
    except SystemExit as exc:
        # idf.py exits the interpreter on success; that must not stop the
        # caller from running the next step (set-target -> build).
        if exc.code not in (None, 0):
            raise


def main():
    parser = argparse.ArgumentParser(description="AI Dog firmware build driver")
    parser.add_argument(
        "action",
        nargs="?",
        default="build",
        choices=["build", "flash", "monitor", "menuconfig", "fullclean", "reconfigure", "erase-flash"],
    )
    parser.add_argument("-p", "--port", default="COM3")
    parser.add_argument("--idf-path", default=None)
    parser.add_argument("--idf-tools-path", default=None)
    parser.add_argument("--activate", default=None)
    parser.add_argument("--no-activate", action="store_true",
                        help="do not read an eim activation profile; use IDF_PATH/PATH as-is")
    parser.add_argument("-b", "--baud", default=None,
                        help="serial baud for flash/monitor (default 460800); "
                             "lower to 115200 if the link drops")
    parser.add_argument("-D", "--define", action="append", default=[], dest="defines",
                        help="extra CMake cache entry (repeatable), e.g. -DCMAKE_VERBOSE_MAKEFILE=1")
    # Hand over to the ESP-IDF venv before doing anything else, so that the
    # real idf.py dependencies are available no matter which python was used.
    ensure_venv_python()

    opts = parser.parse_args()

    if not PROJECT.exists():
        sys.exit("ERROR: project not found: %s" % PROJECT)
    if not DEFAULTS.exists():
        sys.exit("ERROR: board defaults not found: %s" % DEFAULTS)

    activate = None if opts.no_activate else find_activate_script(opts.activate)
    idf = apply_toolchain_environment(opts.idf_path, opts.idf_tools_path, activate)
    apply_idf_version(idf)
    apply_project_environment()

    print("==> ESP-IDF   : %s (version %s)" % (idf, os.environ.get("ESP_IDF_VERSION")))
    print("==> tools     : %s" % os.environ.get("IDF_TOOLS_PATH"))
    print("==> action    : %s" % opts.action)

    os.chdir(PROJECT)

    # Windows cmd lines top out at 32767 chars. The xiaozhi build has compile
    # commands with hundreds of -I flags, and without this CMake emits a
    # non-response-file rule that ninja cannot spawn:
    #   ninja: fatal: CreateProcess: The parameter is incorrect.
    #   (is the command line too long?)
    # CMake warns that the variable is unused -- that warning is wrong, the
    # RSP_FILE really is generated in build.ninja.
    global_args = []
    if os.name == "nt":
        global_args.append("-DCMAKE_NINJA_FORCE_RESPONSE_FILE=1")
    global_args += ["-D" + d for d in opts.defines]
    if opts.baud:
        global_args += ["-b", str(opts.baud)]

    args = []
    if opts.action in ("flash", "monitor"):
        args += ["-p", opts.port]
    args.append(opts.action)

    if opts.action == "build" and not (
        (PROJECT / "sdkconfig").exists() and (PROJECT / "build").exists()
    ):
        print("==> first build: setting target esp32s3")
        run_idf(idf, global_args + ["set-target", "esp32s3"])

    run_idf(idf, global_args + args)
    print("==> idf.py %s done" % opts.action)


if __name__ == "__main__":
    main()
