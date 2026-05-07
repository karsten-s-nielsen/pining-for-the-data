terraform {
  backend "s3" {
    bucket         = "karstenskyt-terraform-state"
    key            = "pining-for-the-data/dev/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "pining-for-the-data-tflock"
  }
}
