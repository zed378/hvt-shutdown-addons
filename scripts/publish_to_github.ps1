# PowerShell script to publish Helm chart to GitHub as a Helm repository
# Usage: .\scripts\publish_to_github.ps1
#
# This script:
# 1. Packages the Helm chart
# 2. Generates index.yaml with correct GitHub Pages URL
# 3. Pushes to the 'pages' branch
# 4. GitHub Pages will serve the files automatically
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
    [string]$Owner = "zed378",
    [string]$RepoName = "hvt-shutdown-addons",
    [string]$Branch = "pages"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$HelmRepoUrl = "https://${Owner}.github.io/${RepoName}"
$GithubRepo = "https://github.com/${Owner}/${RepoName}.git"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ChartDir = Join-Path $ProjectRoot "Charts"
$OutputDir = Join-Path $ScriptDir "..\charts-output"

Write-Host ""
Write-Host "=== GitHub Helm Repository Publisher ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Repository: $GithubRepo" -ForegroundColor Yellow
Write-Host "Branch: $Branch" -ForegroundColor Yellow
Write-Host "Helm Repo URL: $HelmRepoUrl" -ForegroundColor Yellow
Write-Host ""

# Check prerequisites
foreach ($cmd in @("gh", "helm", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Error: $cmd is not installed." -ForegroundColor Red
        exit 1
    }
}

# Check GitHub authentication
try {
    gh auth status 2>&1 | Out-Null
    Write-Host "GitHub CLI: Authenticated" -ForegroundColor Green
} catch {
    Write-Host "Error: GitHub CLI is not authenticated. Run 'gh auth login' first." -ForegroundColor Red
    exit 1
}

Write-Host "Owner: $Owner" -ForegroundColor Yellow
Write-Host "Repo: $RepoName" -ForegroundColor Yellow
Write-Host ""

# Confirm
$confirm = Read-Host "Publish Helm chart to GitHub? (y/n)"
if ($confirm -ne 'y' -and $confirm -ne 'Y') {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}

# Package the Helm chart
Write-Host "=== Packaging Helm chart ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
helm package $ChartDir --destination $OutputDir

# Find the packaged chart
$ChartTgz = Get-ChildItem "$OutputDir\*.tgz" | Select-Object -First 1
if (-not $ChartTgz) {
    Write-Host "Error: Failed to package Helm chart." -ForegroundColor Red
    exit 1
}

$ChartFilename = $ChartTgz.Name
Write-Host "Chart: $ChartFilename" -ForegroundColor Green

# Generate index.yaml with correct URL
Write-Host "Generating index.yaml..." -ForegroundColor Cyan
helm repo index --url $HelmRepoUrl "$OutputDir"

Write-Host ""
Write-Host "=== index.yaml content ===" -ForegroundColor Cyan
Get-Content "$OutputDir\index.yaml"
Write-Host ""

# Clone the target branch
Write-Host "=== Cloning branch '$Branch' ===" -ForegroundColor Cyan
$TempDir = New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "temp-gh-pages-clone") | Out-Null

if (git clone --branch $Branch --single-branch $GithubRepo "$TempDir\clone" 2>$null) {
    Write-Host "Branch '$Branch' found." -ForegroundColor Green
} else {
    Write-Host "Branch '$Branch' not found. Creating new branch from main..." -ForegroundColor Yellow
    git clone $GithubRepo "$TempDir\clone"
    Set-Location "$TempDir\clone"
    git checkout main 2>$null
    git checkout -b $Branch
    Set-Location $ProjectRoot
}

$CloneDir = Join-Path $TempDir "clone"

# Create charts directory in clone
New-Item -ItemType Directory -Force -Path (Join-Path $CloneDir "charts") | Out-Null

# Copy chart files
Copy-Item $ChartTgz.FullName (Join-Path $CloneDir "charts\$ChartFilename")
Copy-Item (Join-Path $OutputDir "index.yaml") (Join-Path $CloneDir "index.yaml")

# Commit and push
Set-Location $CloneDir
git add "charts/$ChartFilename" index.yaml
git config user.email "${Owner}@users.noreply.github.com"
git config user.name $Owner
git commit -m "Add helm chart $ChartFilename" --allow-empty 2>$null | Out-Null
Write-Host "No changes to commit" -ForegroundColor Yellow

git push origin $Branch

Write-Host ""
Write-Host "=== Chart published successfully ===" -ForegroundColor Green
Write-Host ""
Write-Host "Chart location: https://${Owner}.github.io/${RepoName}/charts/${ChartFilename}" -ForegroundColor Green
Write-Host "Index file: https://${Owner}.github.io/${RepoName}/index.yaml" -ForegroundColor Green
Write-Host ""
Write-Host "To install:" -ForegroundColor Cyan
Write-Host "  helm repo add hvt-shutdown ${HelmRepoUrl}" -ForegroundColor White
Write-Host "  helm repo update" -ForegroundColor White
Write-Host "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system" -ForegroundColor White
Write-Host ""
Write-Host "NOTE: If you get 404 errors, enable GitHub Pages:" -ForegroundColor Yellow
Write-Host "  1. Go to https://github.com/${Owner}/${RepoName}/settings/pages" -ForegroundColor White
Write-Host "  2. Source: Deploy from a branch" -ForegroundColor White
Write-Host "  3. Branch: pages (root /)" -ForegroundColor White
Write-Host "  4. Save" -ForegroundColor White

# Cleanup
Set-Location $ProjectRoot
Remove-Item -Path $TempDir -Recurse -Force