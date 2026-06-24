terraform {
  backend "s3" {
    bucket         = "aiops-terraform-state-350480401763"
    key            = "dev/terraform.tfstate"
    region         = "ap-south-1"
    use_lockfile   = true
    encrypt        = true
  }
}