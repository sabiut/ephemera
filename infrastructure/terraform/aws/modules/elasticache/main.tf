# Generate random auth token for Redis
resource "random_password" "redis_auth_token" {
  length  = 32
  special = false  # Redis auth token doesn't support special chars
}

# Store auth token in AWS Secrets Manager
resource "aws_secretsmanager_secret" "redis_auth_token" {
  name_prefix = "${var.cluster_name}-redis-auth-"
  description = "Redis auth token for ${var.cluster_name}"

  tags = var.tags
}

resource "aws_secretsmanager_secret_version" "redis_auth_token" {
  secret_id     = aws_secretsmanager_secret.redis_auth_token.id
  secret_string = random_password.redis_auth_token.result
}

# ElastiCache Subnet Group
resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.cluster_name}-redis-subnet"
  subnet_ids = var.private_subnet_ids

  tags = var.tags
}

# ElastiCache Parameter Group
resource "aws_elasticache_parameter_group" "main" {
  family = "redis7"
  name   = "${var.cluster_name}-redis-params"

  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lru"
  }

  tags = var.tags
}

# ElastiCache Replication Group
resource "aws_elasticache_replication_group" "main" {
  replication_group_id       = "${var.cluster_name}-redis"
  replication_group_description = "Redis for Ephemera ${var.environment}"

  engine               = "redis"
  engine_version       = "7.0"
  node_type            = var.redis_node_type
  num_cache_clusters   = var.environment == "prod" ? 2 : 1
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.main.name

  # Enable automatic failover for production
  automatic_failover_enabled = var.environment == "prod" ? true : false
  multi_az_enabled          = var.environment == "prod" ? true : false

  # Networking
  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [var.security_group_id]

  # Encryption
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                = random_password.redis_auth_token.result

  # Maintenance
  maintenance_window = "mon:05:00-mon:06:00"
  snapshot_window    = "04:00-05:00"
  snapshot_retention_limit = var.environment == "prod" ? 7 : 1

  # Apply changes immediately in dev
  apply_immediately = var.environment == "dev" ? true : false

  # Backup
  auto_minor_version_upgrade = true

  tags = var.tags
}
