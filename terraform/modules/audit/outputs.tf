output "audit_bucket_name" {
  description = "Name of the audit log bucket"
  value       = aws_s3_bucket.audit.id
}

output "audit_bucket_arn" {
  description = "ARN of the audit log bucket"
  value       = aws_s3_bucket.audit.arn
}

output "trail_arn" {
  description = "ARN of the CloudTrail trail"
  value       = aws_cloudtrail.data_bucket.arn
}
