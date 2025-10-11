# Network Load Balancer for nginx-ingress
# This NLB routes traffic to EKS worker nodes running nginx-ingress on NodePort

resource "aws_lb" "ingress" {
  name               = "${var.cluster_name}-ingress-nlb"
  internal           = false
  load_balancer_type = "network"
  subnets            = var.public_subnet_ids

  enable_deletion_protection = false
  enable_cross_zone_load_balancing = true

  tags = merge(
    var.tags,
    {
      Name = "${var.cluster_name}-ingress-nlb"
      Purpose = "nginx-ingress"
    }
  )
}

# Target Group for HTTP (port 80 -> NodePort 30080)
resource "aws_lb_target_group" "http" {
  name     = "${var.cluster_name}-ingress-http"
  port     = var.http_nodeport
  protocol = "TCP"
  vpc_id   = var.vpc_id
  target_type = "instance"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 10
    interval            = 30
    protocol            = "TCP"
    port                = var.http_nodeport
  }

  deregistration_delay = 30

  tags = merge(
    var.tags,
    {
      Name = "${var.cluster_name}-ingress-http"
    }
  )
}

# Target Group for HTTPS (port 443 -> NodePort 30443)
resource "aws_lb_target_group" "https" {
  name     = "${var.cluster_name}-ingress-https"
  port     = var.https_nodeport
  protocol = "TCP"
  vpc_id   = var.vpc_id
  target_type = "instance"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 10
    interval            = 30
    protocol            = "TCP"
    port                = var.https_nodeport
  }

  deregistration_delay = 30

  tags = merge(
    var.tags,
    {
      Name = "${var.cluster_name}-ingress-https"
    }
  )
}

# Listener for HTTP (80 -> NodePort)
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.ingress.arn
  port              = "80"
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.http.arn
  }
}

# Listener for HTTPS (443 -> NodePort)
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.ingress.arn
  port              = "443"
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.https.arn
  }
}

# Note: Target group attachments will be added automatically by EKS worker nodes
# Worker nodes will register themselves to target groups via tags
# Add these tags to EKS worker nodes in the EKS module configuration
