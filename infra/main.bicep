// Hosting for Mentora AI: a registry, somewhere to run, and an identity that is allowed to
// talk to the Cosmos, Search, Storage and Foundry accounts that already exist.
//
// The backing services are NOT created here. They were provisioned by the scripts in
// scripts/ and hold real data, so this template only reads them to hang role assignments
// off. Renaming them here would point the app at resources that do not exist.
//
// Deployed in two passes. The identity and its grants come first, so AcrPull already exists
// by the time the app tries to pull. A system-assigned identity cannot do that, because it
// does not exist until the app does.

targetScope = 'resourceGroup'

@description('Location for the new hosting resources. Matches the existing services.')
param location string = resourceGroup().location

@description('Short name used as the stem for every resource this template creates.')
param appName string = 'mentora'

@description('Create the container app. False on the first pass, when the registry is still empty and there is nothing to pull.')
param deployApp bool = false

@description('Image the container runs, including tag. Only read when deployApp is true.')
param containerImage string = ''

@description('Signs session tokens. Never defaulted, because a default in source would be the same signing key on every deployment.')
@secure()
param jwtSecret string = ''

// --- the services that already exist -------------------------------------------------

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: 'cosmos-learnforge-hc1'
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: 'stlearnforgehc1'
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: 'srch-learnforge-hc1'
}

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: 'aisvc-learnforge-hc1'
}

// The account's own `endpoint` is the classic cognitiveservices.azure.com host, and the
// project path does not exist there: using it returns 403 on the first model call. The
// project publishes the host that does serve it.
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: foundry
  name: 'learnforge'
}

// --- identity ------------------------------------------------------------------------

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${appName}'
  location: location
}

// --- hosting ---------------------------------------------------------------------------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${appName}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: '${appName}acr${uniqueString(resourceGroup().id)}'
  location: location
  sku: { name: 'Basic' }
  properties: {
    // The app pulls with its managed identity, so the admin user stays off and there is no
    // registry password to leak.
    adminUserEnabled: false
    anonymousPullEnabled: false
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${appName}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// --- what the identity is allowed to do ----------------------------------------------

// Scoped to each service rather than the resource group: the app has no business writing to
// anything here that it does not name.

resource pullImages 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

resource writeBlobs 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}

// Two roles, not one. Data Contributor reads and writes documents; the app also creates the
// index on first use, and that is a control-plane call this does not cover.
resource readWriteIndex 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, identity.id, '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
  scope: search
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
  }
}

resource manageIndex 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, identity.id, '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
  scope: search
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
  }
}

// Cognitive Services *OpenAI* User is the tempting one and it does not work: its dataActions
// are all under accounts/OpenAI/, and inference through a Foundry project endpoint is not on
// that path, so the model call returns 403. This role's dataActions are Microsoft.
// CognitiveServices/*, which covers both the chat model and the embeddings. Foundry User
// would also work and additionally grants Microsoft.Support/*, which an app has no use for.
resource callModels 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, identity.id, 'a97b65f3-24c7-4388-baec-2e87135dc908')
  scope: foundry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
}

// Reaching the models through a *project* endpoint is not the same permission as calling the
// account, and the chat client uses the project path.
resource useProject 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, identity.id, '64702f94-c441-49e6-a78b-ef80e0188fee')
  scope: foundry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '64702f94-c441-49e6-a78b-ef80e0188fee')
  }
}

// Cosmos keeps its own role system. Owner on the subscription grants nothing here, which is
// why a deployment can come up green and then 403 on the first request it serves.
resource readWriteData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, identity.id, '00000000-0000-0000-0000-000000000002')
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: cosmos.id
  }
}

// --- the app -------------------------------------------------------------------------

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: 'ca-${appName}'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        // uvicorn binds 8000 in the Dockerfile CMD; a mismatch here is a silent 502.
        targetPort: 8000
        transport: 'auto'
      }
      secrets: [
        { name: 'jwt-secret', value: jwtSecret }
      ]
      registries: [
        { server: registry.properties.loginServer, identity: identity.id }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: containerImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'APP_ENV', value: 'azure' }
            { name: 'LOG_LEVEL', value: 'INFO' }
            // DefaultAzureCredential has to be told which user-assigned identity to present.
            // Without this it cannot choose, and every Azure call fails as unauthorised.
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: project.properties.endpoints['AI Foundry API'] }
            { name: 'FOUNDRY_MODEL_DEPLOYMENT_NAME', value: 'gpt-5-mini' }
            { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
            { name: 'COSMOS_DATABASE', value: 'learnforge' }
            { name: 'BLOB_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
            { name: 'SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
            { name: 'SEARCH_INDEX', value: 'course-passages' }
            { name: 'EMBEDDING_DEPLOYMENT', value: 'text-embedding-3-small' }
            { name: 'EMBEDDING_DIMENSIONS', value: '512' }
            { name: 'JWT_SECRET', secretRef: 'jwt-secret' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              // Startup opens the search and embedding clients in the background, so the
              // first seconds are busy; a tighter delay restarts a container that is fine.
              initialDelaySeconds: 20
              periodSeconds: 30
              failureThreshold: 5
            }
          ]
        }
      ]
      scale: {
        // Not zero. A course takes about twenty minutes in an in-process background task and
        // sends no requests while it runs, so a scaler that counts requests would decide the
        // app was idle and kill the run.
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
  dependsOn: [pullImages]
}

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output identityClientId string = identity.properties.clientId
output appUrl string = deployApp ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
