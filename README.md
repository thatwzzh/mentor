# Mentor

## 启动服务

**启动基础设施环境**

```shell
make docker-recreate
```

**启动Agent服务**

```shell
# 开发模式
make dev
# debug模式
make debug
```

## 服务说明

### 常用服务

- **Agent服务**

  API访问地址：http://localhost

  langsmith调试地址：https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:80

- **Langfuse 管理后台**

  访问地址： http://localhost:3000
  
- **LiteLLM**

  管理后台：http://localhost:4000/ui/
  
  账号：admin
  
  密码：123456
  
  API 地址：http://localhost:4000/v1

- **Nacos 管理后台**

  访问地址：http://localhost:8080/
  
  账号：nacos
  
  密码：初始界面自行设定

  API接口文档：https://nacos.io/swagger/client/
    
- **OpenSandbox Server**

  API 接口：http://127.0.0.1:8090/docs

### 不常用服务

- MinIO 管理后台

  访问地址： http://localhost:9001

  账号：root

  密码：12345678

