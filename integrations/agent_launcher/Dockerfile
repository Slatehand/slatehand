FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
# 软件源只在构建期生效（由启动器按 SLATEHAND_AGENT_BUILD_MIRROR 传入），不进入运行时环境。
ARG APT_MIRROR=deb.debian.org
ARG NPM_REGISTRY=https://registry.npmjs.org
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends ca-certificates curl socat xvfb xclip && rm -rf /var/lib/apt/lists/*
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g --registry="$NPM_REGISTRY" --@xai-official:registry=https://registry.npmjs.org @earendil-works/pi-coding-agent@latest @openai/codex@latest @deepseek-ai/dsh@latest @xai-official/grok@latest @anthropic-ai/claude-code@latest \
    && curl --compressed -fsSL https://antigravity.google/cli/install.sh -o /tmp/agy-install.sh \
    && bash /tmp/agy-install.sh --dir /usr/local/bin \
    && rm /tmp/agy-install.sh \
    && curl --compressed -fsSL https://code.kimi.com/kimi-code/install.sh -o /tmp/kimi-install.sh \
    && KIMI_INSTALL_DIR=/usr/local KIMI_NO_MODIFY_PATH=1 bash /tmp/kimi-install.sh \
    && rm /tmp/kimi-install.sh \
    && pip install --no-cache-dir --index-url "$PIP_INDEX_URL" mcp==2.2.0
WORKDIR /workspace
COPY --chmod=0755 integrations /app/integrations
# 共享 wire 契约（零依赖）：MCP adapter 的错误信封来自这里，不进 Core 业务代码。
COPY --chmod=0755 slatehand_contracts /app/slatehand_contracts
# MCP server instructions 引用的一致性规则原文（单一事实源在 Core skills）。
COPY --chmod=0755 core /app/core
COPY --chmod=0644 client.py gateway.py clipboard_input.py PROMPT.md PLAYGROUND_PROMPT.md dsh-slatehand.cordis.yml /app/
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PI_CODING_AGENT_DIR=/pi NODE_USE_ENV_PROXY=1 NO_PROXY=gateway,localhost,127.0.0.1
ENV CODEX_HOME=/codex
ENV KIMI_CODE_HOME=/kimi-code
ENV GROK_HOME=/grok
ENV CLAUDE_CONFIG_DIR=/claude DISABLE_AUTOUPDATER=1 ENABLE_CLAUDEAI_MCP_SERVERS=false MAX_MCP_OUTPUT_TOKENS=100000
ENV DSH_HOME=/dsh DSH_PERMISSION_MODE=danger-full-access DSH_TELEMETRY_MODE=DISABLED
ENV npm_config_cache=/tmp/npm-cache
USER 10002:10002
