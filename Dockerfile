# 装明白 · 可计算审计服务容器化构建文件
# 基础镜像：Python 3.13 slim 版本（约 200MB，最小化攻击面）
FROM python:3.13-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量：禁止 Python 自动写入字节码，开启无缓冲输出
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 步骤 1：先复制 requirements.txt（利用 Docker 层缓存，依赖不变时无需重装）
COPY requirements.txt .

# 使用清华 PyPI 镜像源加速安装，no-cache-dir 减小镜像体积
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

# 步骤 2：再复制源码（源码变更时此层才会重建）
COPY . .

# 暴露 8000 端口供外部访问
EXPOSE 8000

# 健康检查：slim 镜像不含 curl，改用 Python 标准库探活（原版用 curl 会一直 unhealthy）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status==200 else 1)"

# 启动命令：使用 uvicorn 启动 FastAPI 应用，监听所有网络接口
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
