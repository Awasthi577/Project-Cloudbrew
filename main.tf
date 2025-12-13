# AWS Provider
provider "aws" {
  region = "us-east-1"
}

# GCP Provider
provider "google" {
  project = "exemplary-datum-465405-u3" # Placeholder required for initialization
  region  = "us-central1"
}

# Azure Provider
provider "azurerm" {
  features {} # Required block for Azure
} 