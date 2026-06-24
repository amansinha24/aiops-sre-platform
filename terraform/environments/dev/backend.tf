terraform {
  backend "s3" {
    bucket         = "aiops-terraform-state-350480401763"
    key            = "dev/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "aiops-terraform-locks"
    encrypt        = true
  }
}