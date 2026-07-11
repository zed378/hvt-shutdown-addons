# PowerShell script to publish the Helm charts to GitHub Pages
# Usage: .\scripts\publish_to_github.ps1
#
# Publishes BOTH charts to the same Helm repo (GitHub Pages 'pages' branch):
#   - Charts/          -> node-shutdown           (the shutdown feature add-on)
#   - DashboardChart/  -> harvester-dashboard      (the custom UI / ui-index)
#
# GitHub Pages serves the Helm chart repository at:
#   https://zed378.github.io/hvt-shutdown-addons

param(
    [string]$Owner    = "zed378",
    [string]$RepoName = "hvt-shutdown-addons",
    [string]$Branch   = "pages"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Configuration ──────────────────────────────────────────────────────
$HelmRepoUrl = "https://${Owner}.github.io/${RepoName}"
$GithubRepo  = "https://github.com/${Owner}/${RepoName}.git"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# Every chart directory to package + publish.
$ChartDirs   = @("Charts", "DashboardChart", "NetbirdChart")

$IndexYamlFile = Join-Path $ProjectRoot "index.yaml"
$ReadmeFile    = Join-Path $ProjectRoot "README.md"
$TempDir       = Join-Path $env:TEMP ("hvt-shutdown-pages-" + [Guid]::NewGuid().ToString())

Write-Host ""
Write-Host "=== GitHub Pages Publisher (multi-chart) ===" -ForegroundColor Cyan
Write-Host "Repository:  $GithubRepo" -ForegroundColor Yellow
Write-Host "Helm Repo:   $HelmRepoUrl" -ForegroundColor Yellow
Write-Host ""

# ── Prerequisites ──────────────────────────────────────────────────────
foreach ($cmd in @("helm", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Error: '$cmd' is not installed." -ForegroundColor Red; exit 1
    }
}
$remoteUrl = git remote get-url origin 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: No git remote 'origin' configured." -ForegroundColor Red; exit 1
}
Write-Host "Git remote: $remoteUrl" -ForegroundColor Green
Write-Host ""

# ── Step 1: Package each chart + build a combined index.yaml ────────────
Write-Host "=== [1/2] Packaging charts ===" -ForegroundColor Cyan
$TgzFiles = @()
foreach ($dir in $ChartDirs) {
    $chartPath = Join-Path $ProjectRoot $dir
    $chartYaml = Join-Path $chartPath "Chart.yaml"
    if (-not (Test-Path $chartYaml)) {
        Write-Host "Skip: $dir has no Chart.yaml" -ForegroundColor Yellow; continue
    }
    $name    = (Select-String -Path $chartYaml -Pattern '^name:\s*(.+)').Matches.Groups[1].Value.Trim()
    $version = (Select-String -Path $chartYaml -Pattern '^version:\s*(.+)').Matches.Groups[1].Value.Trim()
    $tgz     = Join-Path $ProjectRoot "${name}-${version}.tgz"

    helm package $chartPath --destination $ProjectRoot
    if (-not (Test-Path $tgz)) {
        Write-Host "Error: Failed to package $dir ($name)." -ForegroundColor Red; exit 1
    }
    Write-Host "Packaged: ${name} v${version}" -ForegroundColor Green
    $TgzFiles += $tgz
}

helm repo index --url $HelmRepoUrl $ProjectRoot
Write-Host ""
Write-Host "=== index.yaml ===" -ForegroundColor Cyan
Get-Content $IndexYamlFile
Write-Host ""

# ── Step 2: Push all tarballs + index + README to the pages branch ──────
Write-Host "=== [2/2] Pushing to '$Branch' branch ===" -ForegroundColor Cyan
$PagesDir = Join-Path $TempDir "pages"
New-Item -ItemType Directory -Force -Path $PagesDir | Out-Null

git clone --branch $Branch --single-branch $GithubRepo $PagesDir 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Cannot clone '$Branch' branch." -ForegroundColor Red
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue; exit 1
}
Write-Host "Cloned '$Branch' branch." -ForegroundColor Green

foreach ($tgz in $TgzFiles) {
    Copy-Item $tgz (Join-Path $PagesDir (Split-Path $tgz -Leaf))
}
Copy-Item $IndexYamlFile (Join-Path $PagesDir "index.yaml")
if (Test-Path $ReadmeFile) { Copy-Item $ReadmeFile (Join-Path $PagesDir "README.md") }

Set-Location $PagesDir
git config user.email "${Owner}@users.noreply.github.com"
git config user.name  $Owner
git add -A
git commit -m "Publish charts: $(($TgzFiles | ForEach-Object { Split-Path $_ -Leaf }) -join ', ')" --allow-empty 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Nothing to commit." -ForegroundColor Yellow }
git push origin $Branch
Write-Host "Pushed: $(($TgzFiles | ForEach-Object { Split-Path $_ -Leaf }) -join ', ') + index.yaml + README.md" -ForegroundColor Green

Set-Location $ProjectRoot

# ── Cleanup ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Cleaning up ===" -ForegroundColor Yellow
foreach ($tgz in $TgzFiles) { Remove-Item $tgz -Force -ErrorAction SilentlyContinue }
Remove-Item $IndexYamlFile -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
Write-Host "Done." -ForegroundColor Green

Write-Host ""
Write-Host "=== Published successfully ===" -ForegroundColor Green
Write-Host "Helm index:  ${HelmRepoUrl}/index.yaml" -ForegroundColor Green
Write-Host ""
Write-Host "Install order: enable 'harvester-dashboard' add-on (the UI), then feature add-ons (node-shutdown, ...)."
