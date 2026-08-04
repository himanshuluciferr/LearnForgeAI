<#
    Provisions Blob Storage for LearnForge AI: account, private container, and the
    data-plane role assignment that ARM "Owner" does NOT give you.

    Same shape as provision_cosmos.ps1, and safe to re-run.

    Usage:  .\scripts\provision_storage.ps1
#>

param(
    [string]$ResourceGroup = "rg-learnforge",
    [string]$Location      = "eastus",
    [string]$Account       = "stlearnforgehc1",
    [string]$Container     = "courses"
)

$ErrorActionPreference = "Stop"

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

Write-Host "1/4 Storage account '$Account'..." -ForegroundColor Cyan
if (Test-AzResource @("storage", "account", "show", "-g", $ResourceGroup, "-n", $Account)) {
    Write-Host "     already exists, skipping." -ForegroundColor DarkGray
}
else {
    # allow-blob-public-access false: a course names the employee's own systems, so no
    # blob may ever be readable without a token. allow-shared-key-access false forces
    # Entra auth, which is what the app already uses and means no key can leak from .env.
    Invoke-Az storage account create `
        --name $Account `
        --resource-group $ResourceGroup `
        --location $Location `
        --sku Standard_LRS `
        --kind StorageV2 `
        --min-tls-version TLS1_2 `
        --allow-blob-public-access false `
        --allow-shared-key-access false `
        --https-only true
}

Write-Host "2/4 Data-plane role assignment..." -ForegroundColor Cyan
# Storage Blob Data Contributor. Owner grants management rights, not data rights.
$principalId = az ad signed-in-user show --query id -o tsv
$scope = az storage account show -g $ResourceGroup -n $Account --query id -o tsv
# Not "length(@)": PowerShell drops the quotes around an argument with no spaces, and
# cmd.exe then reads the parentheses as its own syntax. A projection has no metacharacters.
$existing = az role assignment list `
    --assignee $principalId --scope $scope `
    --role "Storage Blob Data Contributor" --query "[].id" -o tsv

if ($existing) {
    Write-Host "     already assigned, skipping." -ForegroundColor DarkGray
}
else {
    Invoke-Az role assignment create `
        --assignee $principalId `
        --role "Storage Blob Data Contributor" `
        --scope $scope
}

Write-Host "3/4 Container '$Container'..." -ForegroundColor Cyan
# --auth-mode login because shared keys are switched off above.
if (Test-AzResource @("storage", "container", "show", "-n", $Container, "--account-name", $Account, "--auth-mode", "login")) {
    Write-Host "     already exists, skipping." -ForegroundColor DarkGray
}
else {
    Invoke-Az storage container create `
        --name $Container `
        --account-name $Account `
        --auth-mode login `
        --public-access off
}

Write-Host "4/4 Done. Add this to .env:" -ForegroundColor Green
$url = az storage account show -g $ResourceGroup -n $Account --query primaryEndpoints.blob -o tsv
Write-Host ""
Write-Host "BLOB_ACCOUNT_URL=$url"
