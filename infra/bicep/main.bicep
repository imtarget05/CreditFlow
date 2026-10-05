@description('Azure region for CreditFlow resources')
param location string = resourceGroup().location

@description('Environment prefix')
param environment string = 'prod'

@description('ACR login server')
param acrServer string = 'creditflowacr.azurecr.io'

@description('Image tag')
param imageTag string = 'latest'

var uniqueSuffix = substring(uniqueString(resourceGroup().id), 0, 6)
var lawName = 'creditflow-law-${uniqueSuffix}'
var caeName = 'creditflow-cae-${uniqueSuffix}'
var storageAccountName = 'stcreditflow${uniqueSuffix}'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: lawName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource managedEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: caeName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'creditflow-uai-${uniqueSuffix}'
  location: location
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource documentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'credit-documents'
  properties: {
    publicAccess: 'None'
  }
}

resource modelsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'ml-models'
  properties: {
    publicAccess: 'None'
  }
}

resource creditflowApi 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'creditflow-api'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
      }
    }
    template: {
      containers: [
        {
          name: 'creditflow-api'
          image: '${acrServer}/creditflow-api:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'CREDITFLOW_ENV'
              value: environment
            }
            {
              name: 'PORT'
              value: '8080'
            }
            {
              name: 'BLOB_CONTAINER_DOCUMENTS'
              value: documentsContainer.name
            }
            {
              name: 'BLOB_CONTAINER_MODELS'
              value: modelsContainer.name
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '30'
              }
            }
          }
        ]
      }
    }
  }
}

output apiFqdn string = creditflowApi.properties.configuration.ingress.fqdn
output storageAccountName string = storageAccount.name
output documentsContainerName string = documentsContainer.name
output modelsContainerName string = modelsContainer.name
