# PowerShell script to package and publish the Helm chart to a static file repository
# Usage: .\scripts\publish_chart.ps1 [-OutputDirectory ".\charts-output"]
#
# Example:
#   .\scripts\publish_chart.ps1
#   .\scripts\publish_chart.ps1 -OutputDirectory ".\my-charts"
#
# For GitHub Pages as Helm repository, use: .\scripts\publish_to_github.ps1

param(
    [string]$OutputDirectory = ".\charts-output"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Resolve chart directory (parent of scripts/)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ChartDir = Join-Path $ScriptDir "..\Charts"
$FullOutputDir = Join-Path $ScriptDir "..$OutputDirectory"

Write-Host "Packaging Helm chart..." -ForegroundColor Cyan
helm package $ChartDir --destination $FullOutputDir

Write-Host ""
Write-Host "Generating index.yaml..." -ForegroundColor Cyan
helm repo index --url "https://your-registry.example.com/charts" "$FullOutputDir"

Write-Host ""
Write-Host "Chart published to: $FullOutputDir" -ForegroundColor Green
Write-Host ""
Write-Host "Files created:" -ForegroundColor Yellow
Get-ChildItem $FullOutputDir | Format-Table -AutoSize
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Serve these files via HTTP (e.g., nginx, S3, GitHub Pages)"
Write-Host "2. For GitHub Pages: use .\scripts\publish_to_github.ps1 instead"
Write-Host "3. Update the repo URL in Charts/addon.yaml to your serving URL"
Write-Host "4. Apply the Addon CRD: kubectl apply -f Charts/addon.yaml"
