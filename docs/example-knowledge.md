# 项目环境信息

## 服务列表

| 服务名 | 语言 | Git 仓库 | 负责人 |
|--------|------|---------|--------|
| user-service | Java (Spring Boot) | git.example.com/user-service | 张三 |
| order-service | Java (Spring Boot) | git.example.com/order-service | 李四 |
| payment-service | Go | git.example.com/payment-service | 王五 |
| gateway | Node.js (Express) | git.example.com/gateway | 赵六 |
| web-frontend | React | git.example.com/web-frontend | 孙七 |

## 环境配置

### 开发环境 (dev)
- K8s Namespace: `dev`
- MySQL: `mysql-dev.internal:3306` / DB: `ops_dev`
- Redis: `redis-dev.internal:6379`
- Kafka: `kafka-dev.internal:9092`

### 测试环境 (staging)
- K8s Namespace: `staging`
- MySQL: `mysql-staging.internal:3306` / DB: `ops_staging`
- Redis: `redis-staging.internal:6379`
- Kafka: `kafka-staging.internal:9092`
- 域名: `staging.example.com`

### 生产环境 (prod)
- K8s Namespace: `production`
- MySQL: `mysql-prod.internal:3306` (主从集群, 读写分离)
- Redis: `redis-prod.internal:6379` (Sentinel 集群)
- Kafka: `kafka-prod.internal:9092` (3 Broker)
- 域名: `api.example.com`

## 架构概述

微服务架构，通过 API Gateway (Kong) 统一入口。服务间通信使用 gRPC + Kafka 事件驱动。
配置中心使用 Nacos，注册中心使用 K8s Service Discovery。

## 部署流程 SOP

1. 开发者提交代码到 feature 分支
2. 创建 MR 到 main 分支，触发 CI Pipeline
3. Code Review 通过后合并
4. main 分支自动触发 CD Pipeline，部署到 staging
5. staging 验证通过后，手动触发 prod 部署
6. prod 部署采用金丝雀发布，先 10% 流量，观察 15 分钟后全量
