output "container_app_fqdn" {
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}/docs"
  description = "Public Swagger API URL of CreditFlow"
}
