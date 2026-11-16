param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$releaseDir = Join-Path $repoRoot "release"
$version = "1.7.1"
$archivePath = Join-Path $releaseDir "DouyinSpark_${version}_Windows_x64.zip"
$workRoot = Join-Path ([IO.Path]::GetTempPath()) ("DouyinSparkRelease-" + [Guid]::NewGuid().ToString("N"))
$sourcePath = Join-Path $repoRoot "src"
$specPath = Join-Path $repoRoot "DouyinSparkAuto.spec"
$pythonPath = $env:PYTHONPATH
$localAppData = $env:LOCALAPPDATA

New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
New-Item -ItemType Directory -Path $workRoot -Force | Out-Null

try {
    $env:PYTHONPATH = $sourcePath
    Push-Location $repoRoot
    try {
        & $Python -m unittest discover -s tests 2>&1 | Out-Host
        $testExit = $LASTEXITCODE
        if ($testExit -ne 0) { throw "Tests failed ($testExit); release was not built." }
    }
    finally {
        Pop-Location
        $env:PYTHONPATH = $pythonPath
    }

    $buildPath = Join-Path $workRoot "build"
    $distPath = Join-Path $workRoot "dist"
    & $Python -m PyInstaller --noconfirm --clean --workpath $buildPath --distpath $distPath $specPath 2>&1 | Out-Host
    $buildExit = $LASTEXITCODE
    if ($buildExit -ne 0) { throw "PyInstaller build failed ($buildExit)." }

    $executable = Join-Path $distPath "DouyinSpark.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw "Executable was not produced." }

    $env:LOCALAPPDATA = $workRoot
    $probe = Start-Process -FilePath $executable -ArgumentList "--selftest" -Wait -PassThru -NoNewWindow
    $reportPath = Join-Path $workRoot "DouyinSpark\selftest.json"
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw "Packaged self-test did not produce a report (exit code $($probe.ExitCode))."
    }
    $reportText = [IO.File]::ReadAllText($reportPath, [Text.Encoding]::UTF8)
    $report = $reportText | ConvertFrom-Json
    if (-not $report.ok) { throw "Packaged Playwright/Edge self-test failed: $($report.detail)" }
    Write-Output "Self-test: $($report.detail)"
    Write-Output "Driver: $($report.driver)"
    $env:LOCALAPPDATA = $localAppData

    $pythonBase = & $Python -c "import sys; print(sys.base_prefix)"
    $licenseCandidates = @(
        @{ Source = (Join-Path $pythonBase "LICENSE.txt"); Name = "Python-LICENSE.txt" },
        @{ Source = (Join-Path $pythonBase "tcl\tcl8.6\license.terms"); Name = "TclTk-LICENSE.txt" }
    )
    $sitePackages = & $Python -c "import sysconfig; print(sysconfig.get_path('purelib'))"
    $pyInstallerLicense = Get-ChildItem -LiteralPath $sitePackages -Recurse -Filter "COPYING.txt" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "PyInstaller" } | Select-Object -First 1
    if ($pyInstallerLicense) {
        Copy-Item -LiteralPath $pyInstallerLicense.FullName -Destination (Join-Path $workRoot "PyInstaller-COPYING.txt")
    }
    $playwrightDriver = Join-Path $sitePackages "playwright\driver\LICENSE"
    if (Test-Path -LiteralPath $playwrightDriver) {
        Copy-Item -LiteralPath $playwrightDriver -Destination (Join-Path $workRoot "Playwright-LICENSE.txt")
    }

    $stagedFiles = @()
    $stagedExe = Join-Path $workRoot "DouyinSpark.exe"
    Copy-Item -LiteralPath $executable -Destination $stagedExe
    $stagedFiles += $stagedExe
    Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination (Join-Path $workRoot "README.txt")
    Copy-Item -LiteralPath (Join-Path $repoRoot "CHANGELOG.md") -Destination (Join-Path $workRoot "CHANGELOG.txt")
    Copy-Item -LiteralPath (Join-Path $repoRoot "THIRD_PARTY_NOTICES.md") -Destination (Join-Path $workRoot "THIRD_PARTY_NOTICES.txt")
    $stagedFiles += @(
        (Join-Path $workRoot "README.txt"),
        (Join-Path $workRoot "CHANGELOG.txt"),
        (Join-Path $workRoot "THIRD_PARTY_NOTICES.txt")
    )
    foreach ($license in $licenseCandidates) {
        if (Test-Path -LiteralPath $license.Source) {
            $target = Join-Path $workRoot $license.Name
            Copy-Item -LiteralPath $license.Source -Destination $target
            $stagedFiles += $target
        }
    }
    foreach ($extra in @("PyInstaller-COPYING.txt", "Playwright-LICENSE.txt")) {
        $candidate = Join-Path $workRoot $extra
        if (Test-Path -LiteralPath $candidate) { $stagedFiles += $candidate }
    }
    $stagedFiles = @($stagedFiles | ForEach-Object { [IO.Path]::GetFullPath($_) } | Sort-Object -Unique)

    $exeHash = (Get-FileHash -LiteralPath $stagedExe -Algorithm SHA256).Hash.ToLowerInvariant()
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $workRoot "SHA256SUMS.txt"), "$exeHash  DouyinSpark.exe`r`n", $utf8NoBom)
    $stagedFiles += (Join-Path $workRoot "SHA256SUMS.txt")

    Compress-Archive -LiteralPath $stagedFiles -DestinationPath $archivePath -CompressionLevel Optimal -Force

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $badEntry = $archive.Entries | Where-Object { $_.Length -eq 0 }
        if ($badEntry) { throw "Release archive contains an empty file: $($badEntry.FullName)" }
        if ($archive.Entries.Count -ne $stagedFiles.Count) { throw "Release archive entry count is incorrect." }
    }
    finally {
        $archive.Dispose()
    }

    Write-Output "Release package: $archivePath"
    Write-Output "Executable SHA-256: $exeHash"
    Write-Output ("Executable size: {0:N1} MB" -f ((Get-Item -LiteralPath $stagedExe).Length / 1MB))
    Get-FileHash -LiteralPath $archivePath -Algorithm SHA256 | Format-List
}
finally {
    $env:LOCALAPPDATA = $localAppData
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $resolvedWorkRoot = [IO.Path]::GetFullPath($workRoot)
    if ($resolvedWorkRoot.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolvedWorkRoot).StartsWith("DouyinSparkRelease-")) {
        Remove-Item -LiteralPath $resolvedWorkRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
