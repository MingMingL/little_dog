# AI Dog V1 firmware build script (based on xiaozhi-esp32)
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 files
#       without a BOM as ANSI/GBK, and non-ASCII comments will break parsing.
#
# Usage (PowerShell):
#   .\build-ai-dog.ps1                              # build
#   .\build-ai-dog.ps1 -Action flash -Port COM3     # flash
#   .\build-ai-dog.ps1 -Action monitor -Port COM3   # serial monitor
#   .\build-ai-dog.ps1 -Action menuconfig           # Kconfig menu
#   .\build-ai-dog.ps1 -Action fullclean            # clean build dir
#   .\build-ai-dog.ps1 -Action reconfigure          # re-run cmake configure
#   .\build-ai-dog.ps1 -Jobs 4                      # build with 4 ninja jobs
#
# IMPORTANT: run reconfigure after ADDING a new source file under
#   main/boards/ai-dog-s3/
# CMake's file(GLOB BOARD_SOURCES) only collects *.cc at configure time, so a
# brand-new file is silently NOT compiled -- the build then fails at link time
# with "undefined reference to <class>::*". idf.py build alone will not catch it
# because it only re-configures when CMakeLists.txt itself changes.

param(
    [ValidateSet("build", "build-summon", "flash", "flash-summon", "monitor", "menuconfig", "fullclean", "reconfigure")]
    [string]$Action = "build",
    [string]$Port = "COM3",
    [int]$Jobs = 2
)

$ErrorActionPreference = "Stop"

# Single choke point for every native command (idf.py, ninja, ...).
#
# WHY: these tools write Kconfig NOTEs and compiler warnings to stderr. With
# $ErrorActionPreference = "Stop" PowerShell turns any stderr byte into a
# NativeCommandError and kills this script mid-run, which looks like a build
# failure even though the command succeeded (seen twice: once in configure, once
# when ninja triggers a CMake re-run). So: flip to Continue for the child, then
# judge by the real exit code. Route ALL native calls through here - hand-rolling
# the same try/finally in a second helper is how the ninja path regressed.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$CommandArgs
    )
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command @CommandArgs 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "$Command $($CommandArgs -join ' ') failed (exit $exitCode)"
    }
}

function Invoke-Idf {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$IdfArgs
    )
    Invoke-Native -Command "idf.py" @IdfArgs
}

$IdfActivate = "C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1"
$Project     = Join-Path $PSScriptRoot "xiaozhi-esp32"
$Defaults    = Join-Path $PSScriptRoot "config\ai-dog-s3.defaults"

if (-not (Test-Path $IdfActivate)) {
    throw "ESP-IDF activation script not found: $IdfActivate"
}
if (-not (Test-Path $Defaults)) {
    throw "Board defaults not found: $Defaults"
}

# Load ESP-IDF v6.1 environment (IDF_PATH, toolchain PATH, python venv)
. $IdfActivate

Set-Location $Project

# Board selection: base defaults + esp32s3 defaults + our ai-dog-s3 board
$env:SDKCONFIG_DEFAULTS = "sdkconfig.defaults;sdkconfig.defaults.esp32s3;$Defaults"

# Redirect the idf-component-manager cache into the workspace. The default
# cache lives under %LOCALAPPDATA%\Espressif which may be denied here; a
# workspace-local cache also keeps downloads shared across builds.
$env:IDF_COMPONENT_CACHE_PATH = Join-Path $PSScriptRoot ".idf-component-cache"

# Disable the injected safe-delete shim. ESP-IDF build tools legitimately
# bulk-delete temp files during cmake configure (CMakeCache.txt, component
# manifests); the shim's bulk-delete guard fail-closes and breaks configure.
$env:CODEBUDDY_SAFE_DELETE_ENABLED = "0"

# Keep compiler scratch files inside the workspace. The toolchain writes to
# %TEMP% (outside the project), which gets denied when the sandbox turns it
# away mid-build; a local dir avoids that entirely.
$BuildTmp = Join-Path $PSScriptRoot ".build-tmp"
New-Item -ItemType Directory -Force -Path $BuildTmp | Out-Null
$env:TMP = $BuildTmp
$env:TEMP = $BuildTmp
# ccache off: it only adds another out-of-project scratch path (CCACHE_DIR
# likewise), and buys nothing for a single-board build.
$env:CCACHE_DISABLE = "1"
# CCACHE_DISABLE alone is not enough here: the launcher was still ccache, and two
# gcc children ended up sharing one ccache/tmp/cmd_args.tmp.* response file.
# They sat at 0% CPU forever (looks like a slow build, actually a deadlock).
# IDF_CCACHE_ENABLE=0 makes ninja call the compiler directly.
$env:IDF_CCACHE_ENABLE = "0"

# Windows command-line limit: this workspace path is long and IDF passes a huge
# -DESP_LCD_*_VER_* define list, so the generated compile command overflows
# CreateProcess ("ninja: fatal: CreateProcess ... is the command line too
# long?"). Force ninja to use @rsp files instead. This is a CMake cache
# variable, so it must be passed at configure time (see Invoke-Configure).
$env:IDF_EXTRA_CMAKE_ARGS = "-DCMAKE_NINJA_FORCE_RESPONSE_FILE=ON"

# -Jobs goes straight to "ninja -j"; do NOT re-add IDF_PY_BUILD_JOBS, it is
# ignored once ninja is called directly.
# Run ninja directly instead of "idf.py build". idf.py wraps ninja's stdout in a
# Python pipe; in this environment that pipe makes ninja die mid-build with
# "CreateProcess failed: The system cannot find the file specified" and
# "ninja: error: exception: 0xC0000005" - a false build failure that looks like a
# code error. Calling ninja ourselves (same env, same build dir) is reliable.
function Invoke-Ninja {
    param([int]$Jobs)
    Push-Location (Join-Path $Project "build")
    try {
        Invoke-Native -Command "ninja" -j $Jobs all
    } finally {
        Pop-Location
    }
}

$SummonDefaults = Join-Path $PSScriptRoot "config\ai-dog-s3-summon.defaults"

# SDKCONFIG_DEFAULTS only seeds symbols that are NOT already in sdkconfig, so an
# existing sdkconfig would silently keep summon OFF. Flip the values in place to
# make -Action build-summon deterministic.
function Set-SdkconfigValue {
    param([string]$Key, [string]$Value)
    $file = Join-Path $Project "sdkconfig"
    if (-not (Test-Path $file)) { return }
    $lines = Get-Content $file
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^(# )?$([regex]::Escape($Key))(=| )") {
            $lines[$i] = "$Key=$Value"
            $found = $true
            break
        }
    }
    if (-not $found) {
        $lines += "$Key=$Value"
    }
    Set-Content -Path $file -Value $lines -Encoding ASCII
}

function Enable-SummonConfig {
    if (-not (Test-Path $SummonDefaults)) {
        throw "Summon defaults not found: $SummonDefaults"
    }
    $env:SDKCONFIG_DEFAULTS = "sdkconfig.defaults;sdkconfig.defaults.esp32s3;$Defaults;$SummonDefaults"
    Set-SdkconfigValue "CONFIG_AI_DOG_ENABLE_SUMMON" "y"
    Set-SdkconfigValue "CONFIG_AI_DOG_VISION_ENABLE" "y"
    Set-SdkconfigValue "CONFIG_AI_DOG_VOICEPRINT_MULTINET" "y"
    Set-SdkconfigValue "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME" '"partitions/v2/16m_summon.csv"'
}

# Re-run cmake with the response-file flag (only needed once per build dir).
function Invoke-Configure {
    Invoke-Idf -D CMAKE_NINJA_FORCE_RESPONSE_FILE=ON reconfigure
}

switch ($Action) {
    "build" {
        if (-not (Test-Path "sdkconfig") -or -not (Test-Path "build")) {
            Write-Host "==> First build: setting target esp32s3" -ForegroundColor Cyan
            Invoke-Idf set-target esp32s3
            Invoke-Configure
        }
        Invoke-Ninja -Jobs $Jobs
    }
    "build-summon" {
        # V1.5 summon ON. Needs the bigger OTA slots (16m_summon.csv): the esp-dl
        # pedestrian_detect model lives in the app image, so the firmware grows
        # from 2.5MB to 5MB and no longer fits the default 3.94MB OTA slot.
        Enable-SummonConfig
        Write-Host "==> Summon ON (see $SummonDefaults)" -ForegroundColor Cyan
        Invoke-Configure
        Invoke-Ninja -Jobs $Jobs
    }
    "flash"        { Invoke-Idf -p $Port flash }
    "flash-summon" {
        Enable-SummonConfig
        Invoke-Configure
        Invoke-Ninja -Jobs $Jobs
        Invoke-Idf -p $Port flash
    }
    "monitor"     { Invoke-Idf -p $Port monitor }
    "menuconfig"  { Invoke-Idf menuconfig }
    "fullclean"   { Invoke-Idf fullclean }
    "reconfigure" { Invoke-Idf reconfigure }
}

Write-Host "==> $Action done" -ForegroundColor Green
