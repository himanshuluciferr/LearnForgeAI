<#
    Provisions Cosmos DB for LearnForge AI: account, database, containers, and the
    data-plane role assignment that ARM "Owner" does NOT give you.

    Safe to re-run: every step checks for an existing resource first.

    Usage:  .\scripts\provision_cosmos.ps1
#>

param(
    [string]$ResourceGroup = "rg-learnforge",
    [string]$Location      = "eastus",
    [string]$Account       = "cosmos-learnforge-hc1",
    [string]$Database      = "learnforge"
)

$ErrorActionPreference = "Stop"
$infra = Join-Path $PSScriptRoot "..\infra\cosmos"

function Invoke-Az {
    # EAP=Stop ignores native exit codes, so every az call is checked explicitly.
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AzArgs)
    az @AzArgs --only-show-errors | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "az $($AzArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Test-AzResource([string[]]$ShowArgs) {
    # az writes to stderr when a resource is missing, which EAP=Stop would turn into a throw.
    $ErrorActionPreference = "Continue"
    az @ShowArgs 2>&1 | Out-Null
    return $LASTEXITCODE -eq 0
}

Write-Host "1/5 Cosmos account '$Account'..." -ForegroundColor Cyan
if (Test-AzResource @("cosmosdb", "show", "-g", $ResourceGroup, "-n", $Account)) {
    Write-Host "     already exists, skipping." -ForegroundColor DarkGray
}
else {
    # Serverless: billed per request with no idle cost, which suits bursty course generation.
    Invoke-Az cosmosdb create `
        --name $Account `
        --resource-group $ResourceGroup `
        --locations "regionName=$Location" "failoverPriority=0" "isZoneRedundant=False" `
        --capabilities EnableServerless `
        --default-consistency-level Session
}

Write-Host "2/5 Database '$Database'..." -ForegroundColor Cyan
if (Test-AzResource @("cosmosdb", "sql", "database", "show", "-g", $ResourceGroup, "-a", $Account, "-n", $Database)) {
    Write-Host "     already exists, skipping." -ForegroundColor DarkGray
}
else {
    Invoke-Az cosmosdb sql database create `
        --resource-group $ResourceGroup `
        --account-name $Account `
        --name $Database
}

# ttl -1 means "TTL is enabled but nothing expires unless a document sets its own".
$containers = @(
    @{ Name = "jobs";         Index = "jobs-index.json";    Ttl = 2592000 }  # 30 days: progress rows are disposable
    @{ Name = "courses";      Index = "courses-index.json"; Ttl = -1 }
    @{ Name = "progress";     Index = "jobs-index.json";    Ttl = -1 }
    @{ Name = "quiz_results"; Index = "jobs-index.json";    Ttl = -1 }
    @{ Name = "chat_history"; Index = "jobs-index.json";    Ttl = 7776000 } # 90 days of mentor chat
    @{ Name = "users";        Index = "jobs-index.json";    Ttl = -1 }      # accounts never expire
)

Write-Host "3/5 Containers..." -ForegroundColor Cyan
foreach ($c in $containers) {
    if (Test-AzResource @("cosmosdb", "sql", "container", "show", "-g", $ResourceGroup, "-a", $Account, "-d", $Database, "-n", $c.Name)) {
        Write-Host "     $($c.Name): already exists, skipping." -ForegroundColor DarkGray
        continue
    }
    Write-Host "     $($c.Name): creating" -ForegroundColor DarkGray
    Invoke-Az cosmosdb sql container create `
        --resource-group $ResourceGroup `
        --account-name $Account `
        --database-name $Database `
        --name $c.Name `
        --partition-key-path "/user_id" `
        --idx "@$(Join-Path $infra $c.Index)" `
        --ttl $c.Ttl
}

Write-Host "4/5 Data-plane role assignment..." -ForegroundColor Cyan
# Control-plane Owner does not grant data access. This built-in id is Cosmos DB Data Contributor.
$dataContributor = "00000000-0000-0000-0000-000000000002"
$principalId = az ad signed-in-user show --query id -o tsv
$existing = az cosmosdb sql role assignment list `
    --resource-group $ResourceGroup --account-name $Account `
    --query "[?principalId=='$principalId'] | length(@)" -o tsv

if ([int]$existing -gt 0) {
    Write-Host "     already assigned, skipping." -ForegroundColor DarkGray
}
else {
    Invoke-Az cosmosdb sql role assignment create `
        --resource-group $ResourceGroup `
        --account-name $Account `
        --role-definition-id $dataContributor `
        --principal-id $principalId `
        --scope "/"
}

Write-Host "5/5 Done. Add these to .env:" -ForegroundColor Green
$endpoint = az cosmosdb show -g $ResourceGroup -n $Account --query documentEndpoint -o tsv
Write-Host ""
Write-Host "COSMOS_ENDPOINT=$endpoint"
Write-Host "COSMOS_DATABASE=$Database"
