<#
.SYNOPSIS
    Validation and Deployment Runner for HAVI MCD Supervisor Hosted Agent
    targeting Azure AI Foundry.

.DESCRIPTION
    1. Validates local environment and configuration in .env
    2. Executes complete local test suite (MAF routing, HITL gates, Hosted Agent server protocol)
    3. Validates connection to Azure AI Foundry Project without Azure Container Apps or secondary gateways
    4. Reports deployment readiness or exact blockers

.USAGE
    .\deploy.ps1
#>

param(
    [switch]$SkipTests = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param($msg) Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "  [FAIL] $msg" -ForegroundColor Red; exit 1 }

# ── 0. Pre-flight Checks ───────────────────────────────────────────────────────
Write-Step "0. Checking Python environment"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Fail "Python not found in PATH."
}
Write-OK "Python found: $(python --version)"

# ── 1. Load .env Configuration ─────────────────────────────────────────────────
Write-Step "1. Reading configuration from .env"

$EnvFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Fail ".env file not found at $EnvFile."
}

$EnvVars = @{}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match "^\s*([^#=\s]+)\s*=(.*)$") {
        $EnvVars[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
    }
}

$FoundryEndpoint = if ($EnvVars.ContainsKey("FOUNDRY_PROJECT_ENDPOINT")) { $EnvVars["FOUNDRY_PROJECT_ENDPOINT"] } else { "" }
$ModelDeployment = if ($EnvVars.ContainsKey("AZURE_AI_MODEL_DEPLOYMENT_NAME") -and $EnvVars["AZURE_AI_MODEL_DEPLOYMENT_NAME"]) { $EnvVars["AZURE_AI_MODEL_DEPLOYMENT_NAME"] } else { "gpt-4o" }
$AuthMethod      = if ($EnvVars.ContainsKey("AZURE_AUTH_METHOD") -and $EnvVars["AZURE_AUTH_METHOD"]) { $EnvVars["AZURE_AUTH_METHOD"] } else { "api_key" }
$OpenAiEndpoint  = if ($EnvVars.ContainsKey("AZURE_OPENAI_ENDPOINT")) { $EnvVars["AZURE_OPENAI_ENDPOINT"] } else { "" }

Write-OK "Auth method:           $AuthMethod"
Write-OK "Model deployment:      $ModelDeployment"
Write-OK "Azure OpenAI endpoint: $OpenAiEndpoint"

# ── 2. Run Test Suite ──────────────────────────────────────────────────────────
if (-not $SkipTests) {
    Write-Step "2. Running local verification tests (MAF routing, validators, hosted server)"
    python -m pytest tests/ -v
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Local tests failed. Fix test failures before deploying."
    }
    Write-OK "All local verification tests passed."
} else {
    Write-Warn "Skipping local tests (--SkipTests)"
}

# ── 3. Validate Foundry Project Prerequisites ──────────────────────────────────
Write-Step "3. Validating Azure AI Foundry Project endpoint"

if ([string]::IsNullOrWhiteSpace($FoundryEndpoint)) {
    Write-Host "`n" + ("=" * 72) -ForegroundColor Yellow
    Write-Host "  DEPLOYMENT BLOCKED: FOUNDRY_PROJECT_ENDPOINT IS NOT CONFIGURED" -ForegroundColor Yellow
    Write-Host ("=" * 72) -ForegroundColor Yellow
    Write-Host "`n  To deploy this custom MAF agent to Azure AI Foundry Hosted Agents," -ForegroundColor White
    Write-Host "  you must supply your actual Azure AI Foundry Project Endpoint in .env:" -ForegroundColor White
    Write-Host "`n    FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project-name>" -ForegroundColor Cyan
    Write-Host "`n  Zero cloud resources or Container Apps were created." -ForegroundColor Gray
    Write-Host ("=" * 72) + "`n" -ForegroundColor Yellow
    exit 1
}

Write-OK "Foundry Project Endpoint: $FoundryEndpoint"

# ── 4. Verify Foundry Connectivity ─────────────────────────────────────────────
Write-Step "4. Verifying Azure AI Foundry Project connection"
python deploy_foundry_agent.py

if ($LASTEXITCODE -ne 0) {
    Write-Fail "Azure AI Foundry Project connection failed."
}

Write-OK "Azure AI Foundry project connectivity verified."
