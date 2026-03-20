output "state_bucket" {
  description = "S3 bucket name for Terraform state"
  value       = aws_s3_bucket.state.id
}

output "lock_table" {
  description = "DynamoDB table name for Terraform state lock"
  value       = aws_dynamodb_table.lock.name
}
