param(
    [Parameter(Mandatory=$true)][string]$Data,
    [string]$Python = "python",
    [string]$Config = "",
    [switch]$Offline
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$Entry = Join-Path $PSScriptRoot "scripts/hpv.py"
$SharedArgs = @("--data", $Data)
if ($Config) { $SharedArgs += @("--config", $Config) }
$TaskDirectory = [System.IO.Path]::GetFullPath($Data)
$SkillDirectory = [System.IO.Path]::GetFullPath($PSScriptRoot)
if ($TaskDirectory -eq $SkillDirectory -or $TaskDirectory.StartsWith($SkillDirectory + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "任务目录必须在 Skill 外" }
$LogDirectory = Join-Path $TaskDirectory "logs"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LauncherLog = Join-Path $LogDirectory ("启动器-" + (Get-Date -Format "yyyyMMdd-HHmmss-ffff") + ".log")
$ExitStatus = 2
try {
    & $Python -B -X utf8 $Entry preflight @SharedArgs 2>&1 | Tee-Object -FilePath $LauncherLog
    $ExitStatus = $LASTEXITCODE
    if ($ExitStatus -eq 0) {
        $RunArgs = @($SharedArgs)
        if ($Offline) { $RunArgs += "--offline" }
        & $Python -B -X utf8 $Entry run @RunArgs 2>&1 | Tee-Object -FilePath $LauncherLog -Append
        $ExitStatus = $LASTEXITCODE
    }
} finally {
    "exit_code=$ExitStatus" | Out-File -LiteralPath $LauncherLog -Append -Encoding utf8
}
exit $ExitStatus
