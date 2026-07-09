# PowerShell script to publish Helm chart to GitHub Pages
# Usage: .\scripts\publish_to_github.ps1
#
# Flow:
#   1. Generate Helm tarball and index.yaml
#   2. Push tarball + index + README to 'pages' branch
#
# GitHub Pages serves the Helm chart repository at:
#   https://zed378.github.io/hvt-shutdown-addons
#
# Requirements:
#   - GitHub CLI (gh) authenticated: gh auth login
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

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ChartDir    = Join-Path $ProjectRoot "Charts"

$ChartYamlPath = Join-Path $ChartDir "Chart.yaml"
$ChartVersion  = (Select-String -Path $ChartYamlPath -Pattern '^version:\s*(.+)').Matches.Groups[1].Value.Trim()
$ChartName     = (Select-String -Path $ChartYamlPath -Pattern '^name:\s*(.+)').Matches.Groups[1].Value.Trim()
$ChartTgzFile  = Join-Path $ProjectRoot "${ChartName}-${ChartVersion}.tgz"
$IndexYamlFile = Join-Path $ProjectRoot "index.yaml"
$ReadmeFile    = Join-Path $ProjectRoot "README.md"
$TempDir       = Join-Path $env:TEMP ("hvt-shutdown-pages-" + [Guid]::NewGuid().ToString())

Write-Host ""
Write-Host "=== GitHub Pages Publisher ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Repository:  $GithubRepo"                    -ForegroundColor Yellow
Write-Host "Branch:      $Branch"                        -ForegroundColor Yellow
Write-Host "Helm Repo:   $HelmRepoUrl"                   -ForegroundColor Yellow
Write-Host "Chart:       ${ChartName} v${ChartVersion}"  -ForegroundColor Yellow
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

if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh auth status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "Warning: gh CLI not authenticated." -ForegroundColor Yellow }
    else                     { Write-Host "GitHub CLI: Authenticated"           -ForegroundColor Green  }
} else {
    Write-Host "Note: gh CLI not found — using git credential manager." -ForegroundColor Yellow
}
Write-Host ""

# ── Step 1: Generate Helm tarball and index.yaml ───────────────────────
Write-Host "=== [1/2] Generating Helm tarball and index.yaml ===" -ForegroundColor Cyan

helm package $ChartDir --destination $ProjectRoot
if (-not (Test-Path $ChartTgzFile)) {
    Write-Host "Error: Failed to package Helm chart." -ForegroundColor Red; exit 1
}
$ChartFilename = Split-Path $ChartTgzFile -Leaf

helm repo index --url $HelmRepoUrl $ProjectRoot

Write-Host "Tarball:  $ChartFilename"  -ForegroundColor Green
Write-Host "Index:    index.yaml"      -ForegroundColor Green
Write-Host ""
Write-Host "=== index.yaml ===" -ForegroundColor Cyan
Get-Content $IndexYamlFile
Write-Host ""

# ── Step 2: Push tarball + index to pages branch ───────────────────────
Write-Host "=== [2/2] Pushing tarball and index to '$Branch' branch ===" -ForegroundColor Cyan
$PagesDir = Join-Path $TempDir "pages"
New-Item -ItemType Directory -Force -Path $PagesDir | Out-Null

git clone --branch $Branch --single-branch $GithubRepo $PagesDir 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Cannot clone '$Branch' branch." -ForegroundColor Red
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue; exit 1
}
Write-Host "Cloned '$Branch' branch." -ForegroundColor Green

Copy-Item $ChartTgzFile  (Join-Path $PagesDir $ChartFilename)
Copy-Item $IndexYamlFile (Join-Path $PagesDir "index.yaml")
if (Test-Path $ReadmeFile) {
    Copy-Item $ReadmeFile (Join-Path $PagesDir "README.md")
} else {
    Write-Host "Warning: README.md not found at $ReadmeFile - skipping." -ForegroundColor Yellow
}

Set-Location $PagesDir
git config user.email "${Owner}@users.noreply.github.com"
git config user.name  $Owner
git add $ChartFilename index.yaml
if (Test-Path (Join-Path $PagesDir "README.md")) { git add README.md }
git commit -m "Publish ${ChartName} v${ChartVersion}" --allow-empty 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Nothing to commit." -ForegroundColor Yellow }
git push origin $Branch
Write-Host "Pushed: $ChartFilename + index.yaml + README.md" -ForegroundColor Green

Set-Location $ProjectRoot

# ── Cleanup ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Cleaning up ===" -ForegroundColor Yellow
Remove-Item $ChartTgzFile  -Force -ErrorAction SilentlyContinue
Remove-Item $IndexYamlFile -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "charts-output") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "releases")      -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
Write-Host "Done." -ForegroundColor Green

Write-Host ""
Write-Host "=== Published successfully ===" -ForegroundColor Green
Write-Host ""
Write-Host "Helm index:  https://${Owner}.github.io/${RepoName}/index.yaml"                       -ForegroundColor Green
Write-Host "Helm chart:  https://${Owner}.github.io/${RepoName}/${ChartFilename}"                  -ForegroundColor Green
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