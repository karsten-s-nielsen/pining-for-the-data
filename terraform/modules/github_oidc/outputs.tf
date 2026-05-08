# ──────────────────────────────────────────────────────────────────────────────
# Module: GitHub OIDC — Outputs
# ──────────────────────────────────────────────────────────────────────────────

output "role_arn" {
  description = "ARN of the IAM role for GitHub Actions to assume via OIDC"
  value       = aws_iam_role.github_actions.arn
}
