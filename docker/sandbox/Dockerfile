FROM node:24-bookworm AS node

FROM python:3.13-bookworm

# 从同为 Debian Bookworm 的官方镜像复制 Node.js 和 npm。
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /uvx /usr/local/bin/

# uv 使用阿里云 PyPI；npm 安装 pnpm、pnpm 安装项目依赖时使用 npmmirror。
ENV UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ \
    NPM_CONFIG_REGISTRY=https://registry.npmmirror.com/ \
    PNPM_CONFIG_REGISTRY=https://registry.npmmirror.com/

RUN npm install --global pnpm@12 \
    && python3 --version \
    && uv --version \
    && node --version \
    && pnpm --version \
    && test "$(pnpm config get registry)" = "https://registry.npmmirror.com/"

# 构建上下文使用项目根目录：docker build -t opensandbox-python-node:1.0 -f docker/sandbox/Dockerfile .
WORKDIR /sandbox_utils
RUN uv init --bare --name sandbox-utils --python 3.13
RUN uv add "aiobotocore==3.9.2"

COPY src/mentor/utils/minio.py /sandbox_utils/minio.py
RUN uv run --locked python -c "from minio import MinIO"

WORKDIR /root/workspace
