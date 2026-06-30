# PowerShell script to publish Helm chart to GitHub as a Helm repository
# Usage: .\scripts\publish_to_github.ps1 [-GithubRepo "https://github.com/username/repo.git"] [-Branch "pages"]
#
# Examples:
#   .\scripts\publish_to_github.ps1
#   .\scripts\publish_to_github.ps1 -GithubRepo "https://github.com/zed378/hvt-shutdown-addons.git" -Branch "pages"
#
# Requirements:
#   - GitHub CLI (gh) authenticated: gh auth login
#   - Helm installed
#   - Git configured with remote origin

param(
    [string]$GithubRepo = "https://github.com/zed378/hvt-shutdown-addons.git",
    [string]$Branch = "pages"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== GitHub Helm Repository Publisher ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Repository: $GithubRepo" -ForegroundColor Yellow
Write-Host "Branch: $Branch" -ForegroundColor Yellow
Write-Host ""

# Check prerequisites
$cmds = @("gh", "helm", "git")
foreach ($cmd in $cmds) {
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

# Extract owner and repo name from URL
$RepoName = [System.IO.Path]::GetFileNameWithoutExtension($GithubRepo)
$Owner = ($GithubRepo -match 'github\.com[/:](?<owner>[^/]+)') | ForEach-Object { $Matches.owner }
if (-not $Owner) {
    $RemoteUrl = git remote get-url origin 2>$null
    if ($RemoteUrl -match 'github\.com[/:](?<owner>[^/]+)') {
        $Owner = $Matches.owner
    }
}
if (-not $Owner) {
    Write-Host "Error: Could not extract GitHub owner from repository URL." -ForegroundColor Red
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

# Setup directories
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ChartDir = Join-Path $ProjectRoot "Charts"
$OutputDir = Join-Path $ProjectRoot "charts-output"
$TempDir = [System.IO.Path]::GetTempFileName()
Remove-Item $TempDir
New-Item -ItemType Directory -Path $TempDir | Out-Null

# Package Helm chart
Write-Host ""
Write-Host "Packaging Helm chart..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
helm package $ChartDir --destination $OutputDir

# Find packaged chart
$ChartTgz = Get-ChildItem "$OutputDir\*.tgz" | Select-Object -First 1
if (-not $ChartTgz) {
    Write-Host "Error: Failed to package Helm chart." -ForegroundColor Red
    Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}

$ChartFilename = $ChartTgz.Name
Write-Host "Chart: $ChartFilename" -ForegroundColor Green

# Generate index.yaml
Write-Host "Generating index.yaml..." -ForegroundColor Cyan
$RepoUrl = "https://$Owner.github.io/$RepoName"
helm repo index --url $RepoUrl "$OutputDir"

# Clone the target branch
Write-Host "Cloning branch '$Branch'..." -ForegroundColor Cyan
$CloneDir = Join-Path $TempDir "clone"

git clone --branch $Branch --single-branch $GithubRepo $CloneDir 2>$null -q || {
    Write-Host "Branch '$Branch' not found. Creating new branch..." -ForegroundColor Yellow
    git clone $GithubRepo $CloneDir 2>$null -q
    Push-Location $CloneDir
    git checkout -b $Branch 2>$null
    Pop-Location
}

# Create charts directory and copy files
New-Item -ItemType Directory -Force -Path (Join-Path $CloneDir "charts") | Out-Null
Copy-Item $ChartTgz.FullName (Join-Path $CloneDir "charts\$ChartFilename")
Copy-Item (Join-Path $OutputDir "index.yaml") (Join-Path $CloneDir "index.yaml")

# Commit and push
Push-Location $CloneDir
git add "charts/$ChartFilename" index.yaml
git config user.email "github-actions@users.noreply.github.com"
git config user.name "GitHub Actions"
git commit -m "Add helm chart $ChartFilename" --allow-empty 2>$null | Out-Null
git push origin $Branch

Write-Host ""
Write-Host "=== Chart Published ===" -ForegroundColor Green
Write-Host ""
Write-Host "Chart URL: https://$Owner.github.io/$RepoName" -ForegroundColor Cyan
Write-Host ""
Write-Host "To install:" -ForegroundColor Yellow
Write-Host "  helm repo add hvt-shutdown https://$Owner.github.io/$RepoName" -ForegroundColor White
Write-Host "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system" -ForegroundColor White

# Cleanup
Pop-Location
Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue