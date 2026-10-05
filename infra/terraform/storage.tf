resource "azurerm_storage_account" "storage" {
  name                     = "stcreditflow${random_string.suffix.result}"
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"

  https_traffic_only_enabled     = true
  allow_nested_items_to_be_public = false

  tags = azurerm_resource_group.rg.tags
}

resource "azurerm_storage_container" "documents" {
  name                  = "credit-documents"
  storage_account_name  = azurerm_storage_account.storage.name
  container_access_type = "private"
}

resource "azurerm_storage_container" "models" {
  name                  = "ml-models"
  storage_account_name  = azurerm_storage_account.storage.name
  container_access_type = "private"
}

resource "azurerm_role_assignment" "blob_contributor" {
  scope                = azurerm_storage_account.storage.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.identity.principal_id
}
