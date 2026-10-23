#!/usr/bin/env pwsh
# Local validation harness for UniXNU-luming on a Windows host (no MacBook needed).
#
# It runs the checks that do not need Xcode:
#   1. TOML parse of every .toml file (bundled Python 3.11+ / tomllib)
#   2. ADT generator with the measured platform constants (no placeholder flag) + --check,
#      which also verifies the measured addresses
#   3. Negative regressions: a tree with a non-measured address is rejected by --check, and
#      a truncated tree is refused
#   4. scripts/check-luming.py
#   5. optionally, if a UniXNU base checkout is found, `unixnu defconfig` + `unixnu status`
#
# Usage (from anywhere):
#   pwsh -File tools/validate.ps1
#   pwsh -File tools/validate.ps1 -BaseRepo D:\civitoip20\UniXNU-main\UniXNU-main -OutRoot D:\civitoip20\out-luming
#
# Exit code 0 = all selected checks passed.
param(
    [string]$Python = 'python',
    [string]$BaseRepo = '',
    [string]$OutRoot = '',
    [switch]$SkipUnixnu,
    [switch]$Strict
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$fail = 0
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("luming-validate-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

function Step([string]$Name, [scriptblock]$Body) {
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) { $script:fail = 1; Write-Host "FAILED: $Name" -ForegroundColor Red }
}

Write-Host "UniXNU-luming validation" -ForegroundColor Green
Write-Host "repository: $repo"
Write-Host "python:     $Python"

# 1. TOML parse -------------------------------------------------------------
Step 'TOML parse (tomllib)' {
    & $Python -c @"
import pathlib, sys, tomllib
root = pathlib.Path(sys.argv[1])
bad = 0
files = sorted(p for p in root.rglob('*.toml') if '.cache' not in p.parts and 'build' not in p.parts and 'out' not in p.parts)
for path in files:
    try:
        tomllib.loads(path.read_text(encoding='utf-8'))
    except Exception as error:
        bad += 1
        print(f'  FAIL {path.relative_to(root)}: {error}')
print(f'  {len(files)} TOML file(s) parsed, {bad} failure(s)')
raise SystemExit(1 if bad else 0)
"@ $repo
}

# 2. ADT generation + --check (measured constants) --------------------------
$adt = Join-Path $repo 'scripts/make-luming-adt.py'
$payloadFlags = @(
    '--dram-size', '0x300000000',
    '--owned-memory-size', '0x40000000',
    '--chip-id', '8015',
    '--unique-chip-id', '0011223344556677',
    '--cpu-regs', '0,0x100,0x200,0x300,0x400,0x500,0x600,0x700',
    '--psci-method', 'smc',
    '--packed-data-ramdisk',
    '--trust-cache',
    '--ipad-identity'
)
$checkFlags = @(
    '--dram-size', '0x300000000',
    '--cpu-regs', '0,0x100,0x200,0x300,0x400,0x500,0x600,0x700',
    '--packed-data-ramdisk',
    '--trust-cache'
)
$tree = Join-Path $tmp 'kernel.macho.adt'
Step 'ADT generation (measured platform addresses, no placeholder flag)' {
    $stderr = & $Python $adt --output $tree @payloadFlags 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host '  FAIL: ADT generation failed'; return 1 }
    $text = ($stderr | Out-String)
    if ($text -notmatch 'MEASURED') { Write-Host '  FAIL: addresses not reported as measured'; return 1 }
    if ($text -match 'NOT BOOTABLE|SYNTHETIC') { Write-Host '  FAIL: measured generation still reports placeholders'; return 1 }
    Write-Host '  ok: tree generated with the measured addresses'
    $global:LASTEXITCODE = 0
}
Step 'ADT --check (structure contract + measured addresses)' {
    & $Python $adt --check --output $tree @checkFlags
}

# 3. Negative regressions ---------------------------------------------------
Step 'ADT --check rejects a non-measured address' {
    $altered = Join-Path $tmp 'altered.adt'
    & $Python $adt --output $altered @payloadFlags --dram-base '0x90000000' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host '  FAIL: could not generate the altered tree fixture'; return 1 }
    & $Python $adt --check --output $altered @checkFlags 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host '  FAIL: --check accepted a tree with a non-measured DRAM base'; return 1 }
    Write-Host '  ok: non-measured address refused'
    $global:LASTEXITCODE = 0
}
Step 'ADT --check refuses a truncated tree' {
    $bad = Join-Path $tmp 'truncated.adt'
    $bytes = [System.IO.File]::ReadAllBytes($tree)
    [System.IO.File]::WriteAllBytes($bad, $bytes[0..($bytes.Length - 17)])
    & $Python $adt @checkFlags --check --output $bad 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host '  FAIL: truncated tree accepted'; return 1 }
    Write-Host '  ok: truncated tree refused'
    $global:LASTEXITCODE = 0
}

# 4. check-luming.py --------------------------------------------------------
Step 'check-luming.py preflight' {
    $scriptArgs = @()
    if ($Strict) { $scriptArgs += '--strict' }
    & $Python (Join-Path $repo 'scripts/check-luming.py') @scriptArgs
}

# 5. unixnu defconfig + status (optional) ----------------------------------
if (-not $SkipUnixnu) {
    if (-not $BaseRepo) {
        $candidates = @(
            (Join-Path (Split-Path -Parent $repo) 'UniXNU-main\UniXNU-main'),
            (Join-Path (Split-Path -Parent $repo) 'UniXNU')
        )
        foreach ($candidate in $candidates) {
            if (Test-Path (Join-Path $candidate 'unixnu')) { $BaseRepo = $candidate; break }
        }
    }
    if (-not $BaseRepo -or -not (Test-Path (Join-Path $BaseRepo 'unixnu'))) {
        Write-Host "`n=== unixnu defconfig/status === skipped (no base checkout; pass -BaseRepo)" -ForegroundColor Yellow
    } else {
        if (-not $OutRoot) { $OutRoot = Join-Path (Split-Path -Parent $repo) 'out-luming' }
        Push-Location $BaseRepo
        try {
            Step 'unixnu defconfig luming_defconfig' {
                & $Python unixnu defconfig luming_defconfig -D $repo --out $OutRoot
            }
            Step 'unixnu status' {
                & $Python unixnu status -D $repo --out $OutRoot
            }
        } finally { Pop-Location }
    }
}

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

Write-Host ''
if ($fail) {
    Write-Host 'VALIDATION FAILED' -ForegroundColor Red
    exit 1
}
Write-Host 'VALIDATION PASSED (static checks only; nothing built, flashed or booted)' -ForegroundColor Green
exit 0
