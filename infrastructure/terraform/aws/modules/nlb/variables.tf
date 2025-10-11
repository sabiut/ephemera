variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where the NLB will be created"
  type        = string
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs for the NLB"
  type        = list(string)
}

variable "http_nodeport" {
  description = "NodePort for HTTP traffic (nginx-ingress)"
  type        = number
  default     = 30080
}

variable "https_nodeport" {
  description = "NodePort for HTTPS traffic (nginx-ingress)"
  type        = number
  default     = 30443
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}
