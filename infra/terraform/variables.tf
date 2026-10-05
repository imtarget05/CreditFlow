variable "resource_group_name" {
  type        = string
  description = "Resource Group Name for CreditFlow"
  default     = "rg-creditflow-prod"
}

variable "location" {
  type        = string
  description = "Azure Region"
  default     = "eastus"
}

variable "environment" {
  type        = string
  description = "Deployment environment"
  default     = "production"
}

variable "acr_server" {
  type        = string
  description = "Azure Container Registry login server"
  default     = "creditflowacr.azurecr.io"
}

variable "image_tag" {
  type        = string
  description = "Container image tag"
  default     = "latest"
}
