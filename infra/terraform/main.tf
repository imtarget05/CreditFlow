terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
    key_vault {
      purge_soft_delete_on_destroy = true
    }
  }
}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location
  tags = {
    Environment = var.environment
    Project     = "CreditFlow"
    ManagedBy   = "Terraform"
  }
}

resource "azurerm_log_analytics_workspace" "logs" {
  name                = "creditflow-law-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = azurerm_resource_group.rg.tags
}

resource "azurerm_container_app_environment" "env" {
  name                       = "creditflow-cae-env"
  resource_group_name        = azurerm_resource_group.rg.name
  location                   = azurerm_resource_group.rg.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.logs.id
  tags                       = azurerm_resource_group.rg.tags
}

resource "azurerm_user_assigned_identity" "identity" {
  name                = "creditflow-uai"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  tags                = azurerm_resource_group.rg.tags
}

resource "azurerm_container_app" "api" {
  name                         = "creditflow-api"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"
  tags                         = azurerm_resource_group.rg.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.identity.id]
  }

  template {
    min_replicas = 0
    max_replicas = 3

    container {
      name   = "creditflow-api"
      image  = "${var.acr_server}/creditflow-api:${var.image_tag}"
      cpu    = 0.5
      memory = "1.0Gi"

      env {
        name  = "CREDITFLOW_ENV"
        value = "production"
      }
      env {
        name  = "PORT"
        value = "8080"
      }
      env {
        name  = "BLOB_CONTAINER_DOCUMENTS"
        value = azurerm_storage_container.documents.name
      }
      env {
        name  = "BLOB_CONTAINER_MODELS"
        value = azurerm_storage_container.models.name
      }
    }

    http_scale_rule {
      name                = "http-rule"
      concurrent_requests = 30
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}
