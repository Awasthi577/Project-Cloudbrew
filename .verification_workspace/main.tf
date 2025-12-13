
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_xray_sampling_rule" "validation_test" {
  name = "validation-test"
  fixed_rate = 1
  host = "mock_value"
  http_method = "mock_value"
  priority = 1
  reservoir_size = 1
  resource_arn = "mock_value"
  service_name = "mock_value"
  service_type = "mock_value"
  url_path = "mock_value"
  version = 1
}