<#
.SYNOPSIS
    Provisions Azure AI Search and an embedding deployment for the mentor's retrieval.

.DESCRIPTION
    Both are optional. With neither, retrieval stays lexical and everything works; with the
    search service alone the index is keyword-only; the embedding deployment adds vectors.

    The Free tier is 50 MB and 3 indexes, and one course is about 170 KB of passages, so a few
    hundred courses fit at no cost. Pass -Sku basic if you outgrow it.

.EXAMPLE
    .\scripts\provision_search.ps1
    .\scripts\provision_search.ps1 -Sku basic -SkipEmbeddings
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = "rg-learnforge",
    [string]$Name = "srch-learnforge-hc1",
    [string]$Location = "eastus",
    [ValidateSet("free", "basic", "standard")]
    [string]$Sku = "free",
    [string]$FoundryAccount = "aisvc-learnforge-hc1",
    [string]$EmbeddingDeployment = "text-embedding-3-small",
    [switch]$SkipEmbeddings
)

$ErrorActionPreference = "Stop"

function Invoke-Az {
    # PowerShell 5.1 does not stop on a native command's exit code, so a failed step would
    # otherwise charge on through the rest of the script against a resource that is not there.
    & az @args
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') failed with $LASTEXITCODE" }
}

function Test-AzResource {
    param([string[]]$Args)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & az @Args 2>$null | Out-Null
    $found = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $previous
    return $found
}

Write-Host "1/4 Search service..." -ForegroundColor Cyan
if (Test-AzResource @("search", "service", "show", "-g", $ResourceGroup, "-n", $Name)) {
    Write-Host "     $Name already exists, skipping." -ForegroundColor DarkGray
} else {
    Invoke-Az search service create --resource-group $ResourceGroup --name $Name `
        --sku $Sku --location $Location --auth-options aadOrApiKey `
        --aad-auth-failure-mode http401WithBearerChallenge
}

Write-Host "2/4 Data-plane role assignment..." -ForegroundColor Cyan
# Control-plane Owner grants no data access here, the same trap as Cosmos. These two are
# Search Index Data Contributor (write) and Search Service Contributor (create the index).
$principalId = az ad signed-in-user show --query id -o tsv
$scope = az search service show -g $ResourceGroup -n $Name --query id -o tsv
foreach ($role in @("Search Index Data Contributor", "Search Service Contributor")) {
    Write-Host "     $role" -ForegroundColor DarkGray
    az role assignment create --assignee $principalId --role $role --scope $scope 2>$null | Out-Null
}

Write-Host "3/4 Embedding deployment..." -ForegroundColor Cyan
if ($SkipEmbeddings) {
    Write-Host "     skipped; the index will be keyword-only." -ForegroundColor DarkGray
} elseif (Test-AzResource @("cognitiveservices", "account", "deployment", "show",
        "-g", $ResourceGroup, "-n", $FoundryAccount, "--deployment-name", $EmbeddingDeployment)) {
    Write-Host "     $EmbeddingDeployment already exists, skipping." -ForegroundColor DarkGray
} else {
    # GlobalStandard because every Standard quota is 0 on this subscription.
    Invoke-Az cognitiveservices account deployment create `
        --resource-group $ResourceGroup --name $FoundryAccount `
        --deployment-name $EmbeddingDeployment `
        --model-name $EmbeddingDeployment --model-version "1" --model-format OpenAI `
        --sku-capacity 120 --sku-name GlobalStandard
}

Write-Host "4/4 Settings for .env" -ForegroundColor Cyan
$endpoint = "https://$Name.search.windows.net"
Write-Host ""
Write-Host "SEARCH_ENDPOINT=$endpoint"
if (-not $SkipEmbeddings) {
    Write-Host "EMBEDDING_DEPLOYMENT=$EmbeddingDeployment"
}
Write-Host ""
Write-Host "Then fill the index for courses that already exist:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\python.exe scripts\backfill_index.py"
