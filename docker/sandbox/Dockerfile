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

WORKDIR /root/workspace
