variable "resource_group_name" {
  type    = string
  default = "rg-portfolio-prod"
}

variable "location" {
  type    = string
  default = "southeastasia"
}

variable "environment" {
  type    = string
  default = "production"
}

variable "acr_name" {
  type    = string
  default = ""
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "cloudflare_account_id" {
  type    = string
  default = ""
}

variable "cloudflare_api_token" {
  type      = string
  default   = ""
  sensitive = true
}
