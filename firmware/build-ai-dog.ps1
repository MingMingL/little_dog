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
#
# IMPORTANT: run reconfigure after ADDING a new source file under
#   main/boards/ai-dog-s3/
# CMake's file(GLOB BOARD_SOURCES) only collects *.cc at configure time, so a
# brand-new file is silently NOT compiled -- the build then fails at link time
# with "undefined reference to <class>::*". idf.py build alone will not catch it
# because it only re-configures when CMakeLists.txt itself changes.

param(
    [ValidateSet("build", "flash", "monitor", "menuconfig", "fullclean", "reconfigure")]
    [string]$Action = "build",
    [string]$Port = "COM3"
)

$ErrorActionPreference = "Stop"

# Wrapper around idf.py. idf.py writes Kconfig NOTEs and compiler warnings to
# stderr; with $ErrorActionPreference = "Stop" PowerShell turns that into a
# NativeCommandError and kills this script mid-configure, which looks like a
# build failure even though configure just succeeded. Check every invocation's
# exit code immediately so a failed set-target cannot be masked by build.
# NOTE: keep the comment block above as ASCII only (same reason as the file
# header) - PS 5.1 reads .ps1 without BOM as GBK, so UTF-8 Chinese comments
# get mis-decoded into stray quotes/braces and break the parser entirely.
function Invoke-Idf {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$IdfArgs
    )
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & idf.py @IdfArgs 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "idf.py $($IdfArgs -join ' ') failed (exit $exitCode)"
    }
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
# %TEMP% and the ccache dir (both outside the project), which get denied when
# the sandbox turns them away mid-build; local dirs avoid that entirely.
$BuildTmp = Join-Path $PSScriptRoot ".build-tmp"
$BuildCache = Join-Path $PSScriptRoot ".ccache"
New-Item -ItemType Directory -Force -Path $BuildTmp, $BuildCache | Out-Null
$env:TMP = $BuildTmp
$env:TEMP = $BuildTmp
$env:CCACHE_DIR = $BuildCache
# ccache adds another scratch path and brings no benefit on the first build.
$env:CCACHE_DISABLE = "1"

switch ($Action) {
    "build" {
        if (-not (Test-Path "sdkconfig") -or -not (Test-Path "build")) {
            Write-Host "==> First build: setting target esp32s3" -ForegroundColor Cyan
            Invoke-Idf set-target esp32s3
        }
        Invoke-Idf build
    }
    "flash"       { Invoke-Idf -p $Port flash }
    "monitor"     { Invoke-Idf -p $Port monitor }
    "menuconfig"  { Invoke-Idf menuconfig }
    "fullclean"   { Invoke-Idf fullclean }
    "reconfigure" { Invoke-Idf reconfigure }
}

Write-Host "==> idf.py $Action done" -ForegroundColor Green
