# PowerShell script to build and publish UI extension + Helm chart to GitHub Pages
# Usage: .\scripts\publish_to_github.ps1
#
# This script does everything in a single run and a single git push:
# 1. Builds the Vue UI extension (yarn build-pkg)
# 2. Packages the Helm chart and generates index.yaml
# 3. Clones the 'pages' branch once and copies all artifacts
# 4. Commits and pushes in one operation
#
# GitHub Pages serves both the Helm repository and UI plugin static files at:
#   https://zed378.github.io/hvt-shutdown-addons
#
# For automated publishing on every push to main, see:
#   .github/workflows/publish-pages.yml
#
# Requirements:
#   - GitHub CLI (gh) authenticated: gh auth login
#   - Node.js 24+ with yarn or npm installed
#   - Helm installed
#   - Git configured with remote origin
#
# BEFORE FIRST USE: Enable GitHub Pages in repository settings:
#   1. Go to https://github.com/zed378/hvt-shutdown-addons/settings/pages
#   2. Source: Deploy from a branch
#   3. Branch: pages (root /)
#   4. Save

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
$UiDir       = "hvt-shutdown-ui"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ChartDir    = Join-Path $ProjectRoot "Charts"
$UiSrcDir    = Join-Path $ProjectRoot $UiDir

# Extract Helm chart metadata
$ChartYamlPath = Join-Path $ChartDir "Chart.yaml"
$ChartVersion  = (Select-String -Path $ChartYamlPath -Pattern '^version:\s*(.+)').Matches.Groups[1].Value.Trim()
$ChartName     = (Select-String -Path $ChartYamlPath -Pattern '^name:\s*(.+)').Matches.Groups[1].Value.Trim()
$ChartTgzFile  = Join-Path $ProjectRoot "${ChartName}-${ChartVersion}.tgz"
$IndexYamlFile = Join-Path $ProjectRoot "index.yaml"
$TempDir       = Join-Path $env:TEMP ("hvt-shutdown-pages-" + [Guid]::NewGuid().ToString())

Write-Host ""
Write-Host "=== GitHub Pages Publisher ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Repository:  $GithubRepo"     -ForegroundColor Yellow
Write-Host "Branch:      $Branch"          -ForegroundColor Yellow
Write-Host "Helm Repo:   $HelmRepoUrl"     -ForegroundColor Yellow
Write-Host "Chart:       ${ChartName} v${ChartVersion}" -ForegroundColor Yellow
Write-Host "UI Source:   $UiSrcDir"        -ForegroundColor Yellow
Write-Host ""

# ── Prerequisites ──────────────────────────────────────────────────────
foreach ($cmd in @("helm", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Error: '$cmd' is not installed." -ForegroundColor Red
        exit 1
    }
}

# Node.js
try { $nodeVer = node --version; Write-Host "Node: $nodeVer" }
catch { Write-Host "Error: Node.js is not installed." -ForegroundColor Red; exit 1 }

# yarn / npm
if (Get-Command yarn -ErrorAction SilentlyContinue) {
    $pkgMgr = "yarn"
    Write-Host "Yarn: $(yarn --version)"
} elseif (Get-Command npm -ErrorAction SilentlyContinue) {
    $pkgMgr = "npm"
    Write-Host "npm:  $(npm --version)"
} else {
    Write-Host "Error: Neither yarn nor npm is installed." -ForegroundColor Red
    exit 1
}
Write-Host ""

# Git remote
$remoteUrl = git remote get-url origin 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: No git remote 'origin' configured." -ForegroundColor Red
    exit 1
}
Write-Host "Git remote: $remoteUrl" -ForegroundColor Green

# GitHub CLI
if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh auth status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Warning: gh CLI not authenticated. Run 'gh auth login'." -ForegroundColor Yellow
    } else {
        Write-Host "GitHub CLI: Authenticated" -ForegroundColor Green
    }
} else {
    Write-Host "Note: gh CLI not found — using git credential manager." -ForegroundColor Yellow
}
Write-Host ""

# ── Step 1: Build UI Extension ─────────────────────────────────────────
Write-Host "=== [1/3] Building UI Extension ===" -ForegroundColor Cyan
Set-Location $UiSrcDir

if (-not (Test-Path "node_modules")) {
    Write-Host "Installing UI dependencies..." -ForegroundColor Yellow
    if ($pkgMgr -eq "yarn") { yarn install --frozen-lockfile } else { npm install }
}

yarn build-pkg $UiDir true

$DistPkgDir = Join-Path $UiSrcDir "dist-pkg"
if (-not (Test-Path $DistPkgDir)) {
    Write-Host "Error: Build output not found at $DistPkgDir" -ForegroundColor Red
    exit 1
}

$versionDir = Get-ChildItem -Directory $DistPkgDir |
    Where-Object { $_.Name -match '^hvt-shutdown-ui-.+' } |
    Select-Object -First 1

if (-not $versionDir) {
    Write-Host "Error: No versioned build directory found in $DistPkgDir" -ForegroundColor Red
    Get-ChildItem $DistPkgDir
    exit 1
}

$UiVersionDir = $versionDir.FullName
$UiVersion    = $versionDir.Name -replace '^hvt-shutdown-ui-', ''
Write-Host "Built UI version: $UiVersion  ->  $UiVersionDir" -ForegroundColor Green
Write-Host ""

Set-Location $ProjectRoot

# ── Step 2: Package Helm Chart ─────────────────────────────────────────
Write-Host "=== [2/3] Packaging Helm Chart ===" -ForegroundColor Cyan

# Copy scripts into Charts/scripts/ for ConfigMap inclusion
New-Item -ItemType Directory -Force -Path (Join-Path $ChartDir "scripts") | Out-Null
Get-ChildItem "$ScriptDir\*.sh" | Where-Object { $_.Name -ne "publish_to_github.sh" } |
    Copy-Item -Destination (Join-Path $ChartDir "scripts")
Get-ChildItem "$ScriptDir\*.ps1"    -ErrorAction SilentlyContinue |
    Copy-Item -Destination (Join-Path $ChartDir "scripts") -ErrorAction SilentlyContinue
Get-ChildItem "$ScriptDir\*.mac.sh" -ErrorAction SilentlyContinue |
    Copy-Item -Destination (Join-Path $ChartDir "scripts") -ErrorAction SilentlyContinue

helm package $ChartDir --destination $ProjectRoot

if (-not (Test-Path $ChartTgzFile)) {
    Write-Host "Error: Failed to package Helm chart." -ForegroundColor Red
    exit 1
}
$ChartFilename = Split-Path $ChartTgzFile -Leaf
Write-Host "Packaged: $ChartFilename" -ForegroundColor Green

helm repo index --url $HelmRepoUrl $ProjectRoot
Write-Host "Generated: index.yaml" -ForegroundColor Green
Write-Host ""
Write-Host "=== index.yaml ===" -ForegroundColor Cyan
Get-Content $IndexYamlFile
Write-Host ""

# ── Step 3: Deploy to pages branch (single clone + single push) ────────
Write-Host "=== [3/3] Deploying to '$Branch' branch ===" -ForegroundColor Cyan
$PagesDir = Join-Path $TempDir "pages"
New-Item -ItemType Directory -Force -Path $PagesDir | Out-Null

git clone --branch $Branch --single-branch $GithubRepo $PagesDir 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Cannot clone '$Branch' branch." -ForegroundColor Red
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "Cloned '$Branch' branch." -ForegroundColor Green

# Copy UI static files
$DestVersionDir = Join-Path $PagesDir "hvt-shutdown-ui-$UiVersion"
if (Test-Path $DestVersionDir) { Remove-Item -Recurse -Force $DestVersionDir }
New-Item -ItemType Directory -Force -Path $DestVersionDir | Out-Null
Copy-Item -Recurse -Path "$UiVersionDir\*" -Destination $DestVersionDir
Copy-Item (Join-Path $UiVersionDir "package.json") (Join-Path $PagesDir "package.json") -Force
Write-Host "Copied UI files -> hvt-shutdown-ui-$UiVersion/" -ForegroundColor Green

# Copy Helm artifacts
Copy-Item $ChartTgzFile  (Join-Path $PagesDir $ChartFilename)
Copy-Item $IndexYamlFile (Join-Path $PagesDir "index.yaml")
Write-Host "Copied Helm artifacts -> $ChartFilename + index.yaml" -ForegroundColor Green

Set-Location $PagesDir

git add "hvt-shutdown-ui-$UiVersion" package.json $ChartFilename index.yaml
git config user.email "${Owner}@users.noreply.github.com"
git config user.name  $Owner
git commit -m "Publish UI v${UiVersion} and ${ChartName} v${ChartVersion}" --allow-empty 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Nothing to commit." -ForegroundColor Yellow }
git push origin $Branch

Set-Location $ProjectRoot

# ── Cleanup ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Cleaning up ===" -ForegroundColor Yellow
$cleanupScripts = @(
    "publish_to_github.sh", "publish_to_github.ps1",
    "publish_chart.sh",     "publish_chart.ps1", "publish_chart.mac.sh",
    "create_release.sh",    "create_release.ps1"
)
foreach ($s in $cleanupScripts) {
    Remove-Item (Join-Path $ChartDir "scripts\$s") -Force -ErrorAction SilentlyContinue
}
Remove-Item $ChartTgzFile  -Force -ErrorAction SilentlyContinue
Remove-Item $IndexYamlFile -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "charts-output") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "releases")      -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
Write-Host "Done." -ForegroundColor Green

Write-Host ""
Write-Host "=== Published successfully ===" -ForegroundColor Green
Write-Host ""
Write-Host "Helm index:   https://${Owner}.github.io/${RepoName}/index.yaml"   -ForegroundColor Green
Write-Host "Helm chart:   https://${Owner}.github.io/${RepoName}/${ChartFilename}" -ForegroundColor Green
Write-Host "UIPlugin URL: https://${Owner}.github.io/${RepoName}"              -ForegroundColor Green
Write-Host "UI plugin:    https://${Owner}.github.io/${RepoName}/hvt-shutdown-ui-${UiVersion}/package/index.html" -ForegroundColor Green
Write-Host ""
Write-Host "To add the Helm repo:" -ForegroundColor Cyan
Write-Host "  helm repo add hvt-shutdown ${HelmRepoUrl}"
Write-Host "  helm repo update"
Write-Host "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system"
Write-Host ""
Write-Host "NOTE: If you get 404 errors, enable GitHub Pages:" -ForegroundColor Yellow
Write-Host "  1. Go to https://github.com/${Owner}/${RepoName}/settings/pages"
Write-Host "  2. Source: Deploy from a branch"
Write-Host "  3. Branch: pages (root /)"
Write-Host "  4. Save"