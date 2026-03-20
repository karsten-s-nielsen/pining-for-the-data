output "bucket_name" {
  description = "S3 bucket name for tracking data"
  value       = aws_s3_bucket.data.id
}

output "bucket_arn" {
  description = "S3 bucket ARN for tracking data"
  value       = aws_s3_bucket.data.arn
}

output "kms_key_arn" {
  description = "KMS key ARN for data encryption"
  value       = aws_kms_key.data.arn
}
